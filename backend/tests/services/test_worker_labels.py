"""The labels level and the two Gmail label tools (BL-173): the agent says a thread and one fixed word; the server does the rest."""
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete, select

from app import composio_tools, gmail_share
from app.db.models import Agent, WorkerLabel, WorkerLabelAction
from app.db.models.routine import Routine, RoutineAppAction, RoutineRun
from app.db.models.user_credential import UserCredential
from app.services import agent_store, composio_bridge, composio_policy, composio_service, worker_label_ops
from app.services.composio_client import ComposioClient
from app.services.provider_adapter import TurnOutcome
from tests.services.test_composio_bridge import KEY, ask, card, one_turn, world  # noqa: F401
from tests.services.test_composio_gmail_share import (  # noqa: F401
    ACCOUNT, GmailFake, entry, gmail_account, grant, rule, thread_in)
from tests.services.thread_harness import cleanup, owner, sessions, wait_until  # noqa: F401

APPLY = composio_tools.LABEL_APPLY
LIST = composio_tools.LABEL_LIST
SYSTEM = {"INBOX", "UNREAD", "TRASH", "SPAM", "SENT", "STARRED", "IMPORTANT", "DRAFT", "CATEGORY_PROMOTIONS"}


class LabelFake(GmailFake):
    """The paging Gmail fake plus labels: a mailbox with system labels and one of the owner's, create and modify-thread."""

    def __init__(self):
        super().__init__()
        self.labels = [{"id": "INBOX", "name": "INBOX", "type": "system"}, {"id": "TRASH", "name": "TRASH", "type": "system"},
                       {"id": "Label_owner1", "name": "Customers", "type": "user"}]
        self.modified = []
        self.modify_down = False
        self.modify_rejected = False
        self.counter = 100

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/execute"):
            body = json.loads(request.content)
            if body["tool_slug"] == "GMAIL_MODIFY_THREAD_LABELS" and (self.modify_down or self.modify_rejected):
                self.executes.append({"slug": body["tool_slug"], "arguments": body["arguments"], "account": body.get("account")})
                if self.modify_down:
                    raise httpx.ConnectError("down")
                return httpx.Response(200, json={"result_type": "failed", "data": None, "error": "refused"})
        return super().handler(request)

    def gmail(self, slug, args):
        if slug == "GMAIL_LIST_LABELS":
            return {"labels": [dict(label) for label in self.labels]}
        if slug == "GMAIL_CREATE_LABEL":
            self.counter += 1
            label = {"id": f"Label_{self.counter}", "name": args["label_name"], "type": "user"}
            self.labels.append(label)
            return {**label, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        if slug == "GMAIL_MODIFY_THREAD_LABELS":
            self.modified.append(args)
            return {"response": {}, "success": True}
        return super().gmail(slug, args)

    def calls(self, slug):
        return [e["arguments"] for e in self.executes if e.get("slug") == slug]


@pytest.fixture(autouse=True)
async def clean_label_tables(sessions):
    """worker_label has no foreign key to the agent, so a finished test would leave its rows for the next."""
    async def wipe():
        async with sessions() as db:
            await db.execute(delete(WorkerLabelAction))
            await db.execute(delete(WorkerLabel))
            await db.commit()
    await wipe()
    yield
    await wipe()


@pytest.fixture
async def fake(monkeypatch):
    fake = LabelFake()
    monkeypatch.setattr(composio_bridge, "make_client", lambda key: ComposioClient(key, transport=httpx.MockTransport(fake.handler)))
    return fake


async def actions(sessions):
    async with sessions() as db:
        return list((await db.execute(select(WorkerLabelAction).order_by(WorkerLabelAction.id))).scalars())


async def recorded_labels(sessions):
    async with sessions() as db:
        return list((await db.execute(select(WorkerLabel).order_by(WorkerLabel.id))).scalars())


async def direct(sessions, owner, agent, tool_name, args, shares=None, *, turn=1001, run=None, account=ACCOUNT):
    """run_tool as the bridge calls it: the share, the account and the record context, with no gate or card."""
    tool = composio_tools.BY_NAME[tool_name]
    sent, _ = composio_tools.clean_arguments(tool, args)
    async with sessions() as db:
        return await composio_service.run_tool(db, owner, lambda key: composio_bridge.make_client(key), tool, sent, None, account_id=account,
                                               allowed_account_ids=[account], shares=shares or {},
                                               mark_context={"agent_id": agent.id, "turn_id": turn, "run_id": run})


async def ready(world, fake, sessions, level="labels", shares=None):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, shares if shares is not None else {}, level=level)
    return runtime, engine, agent


def modify_bodies(fake):
    return fake.calls("GMAIL_MODIFY_THREAD_LABELS")


# -- the name and the ids ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("word", composio_tools.WORKER_LABELS)
async def test_label_name_is_always_prefixed_and_from_the_fixed_vocabulary(world, fake, sessions, owner, word):
    runtime, engine, agent = await ready(world, fake, sessions)
    answer = await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": word})
    assert answer["result"] == {"thread_id": "th7", "label": f"Worker/{word}", "applied": True}
    assert [c["label_name"] for c in fake.calls("GMAIL_CREATE_LABEL")] == [f"Worker/{word}"]


async def test_a_word_outside_the_vocabulary_never_reaches_gmail(world, fake, sessions, owner):
    runtime, engine, agent = await ready(world, fake, sessions)
    answer = await worker_label_ops.apply(None, owner, None, "s", ACCOUNT, {"thread_id": "t", "label": "Worker/Urgent"},
                                          {"agent_id": agent.id, "turn_id": 1}, None)
    assert answer["code"] == "bad_arguments" and fake.executes == []
    with pytest.raises(composio_tools.BadArguments):
        composio_tools.clean_arguments(composio_tools.BY_NAME[APPLY], {"thread_id": "t", "label": "Spam"})


async def test_system_and_foreign_label_ids_are_never_sent(world, fake, sessions, owner):
    runtime, engine, agent = await ready(world, fake, sessions)
    for word in composio_tools.WORKER_LABELS:
        await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": word})
    sent_ids = {i for body in modify_bodies(fake) for i in body.get("add_label_ids", []) + body.get("remove_label_ids", [])}
    assert sent_ids and not sent_ids & SYSTEM and "Label_owner1" not in sent_ids
    mine = {row.gmail_label_id for row in await recorded_labels(sessions)}
    assert sent_ids == mine and len(mine) == len(composio_tools.WORKER_LABELS)
    assert not any(name in json.dumps(modify_bodies(fake)) for name in SYSTEM)


async def test_agent_path_sends_one_added_id_and_no_removed_id(world, fake, sessions, owner):
    runtime, engine, agent = await ready(world, fake, sessions)
    await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": "Urgent"})
    (body,) = modify_bodies(fake)
    assert set(body) == {"user_id", "thread_id", "add_label_ids"}      # no remove list at all
    assert body["thread_id"] == "th7" and len(body["add_label_ids"]) == 1 and body["add_label_ids"][0].startswith("Label_")


async def test_a_label_already_made_is_reused_and_a_deleted_one_is_made_again(world, fake, sessions, owner):
    runtime, engine, agent = await ready(world, fake, sessions)
    await direct(sessions, owner, agent, APPLY, {"thread_id": "th1", "label": "Bug"}, turn=1)
    await direct(sessions, owner, agent, APPLY, {"thread_id": "th2", "label": "Bug"}, turn=2)
    assert len(fake.calls("GMAIL_CREATE_LABEL")) == 1 and len(await recorded_labels(sessions)) == 1
    first = (await recorded_labels(sessions))[0].gmail_label_id
    assert {b["add_label_ids"][0] for b in modify_bodies(fake)} == {first}
    fake.labels = [label for label in fake.labels if label["id"] != first]      # the owner deleted it in Gmail
    await direct(sessions, owner, agent, APPLY, {"thread_id": "th3", "label": "Bug"}, turn=3)
    (row,) = await recorded_labels(sessions)
    assert row.gmail_label_id != first and modify_bodies(fake)[-1]["add_label_ids"] == [row.gmail_label_id]
    assert len(fake.calls("GMAIL_CREATE_LABEL")) == 2


async def test_collision_with_an_existing_worker_label_is_refused(world, fake, sessions, owner):
    runtime, engine, agent = await ready(world, fake, sessions)
    fake.labels.append({"id": "Label_theirs", "name": "worker/urgent", "type": "user"})     # not made by this app, any case
    answer = await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": "Urgent"})
    assert answer["code"] == "label_collision"
    assert fake.calls("GMAIL_CREATE_LABEL") == [] and modify_bodies(fake) == []
    assert await actions(sessions) == [] and await recorded_labels(sessions) == []


# -- the share and the levels --------------------------------------------------------------------------------------------

async def test_thread_outside_the_share_is_refused_before_any_gmail_call(world, fake, sessions, owner):
    shares = {"gmail": {ACCOUNT: entry(5)}}
    runtime, engine, agent = await ready(world, fake, sessions, shares=shares)
    out = thread_in(rule(5), False)
    answer = await direct(sessions, owner, agent, APPLY, {"thread_id": out, "label": "Urgent"}, shares)
    assert answer["code"] == "outside_share" and fake.executes == [] and await actions(sessions) == []
    inside = await direct(sessions, owner, agent, APPLY, {"thread_id": thread_in(rule(5)), "label": "Urgent"}, shares)
    assert inside["result"]["applied"] is True


async def test_a_damaged_share_denies_labelling(world, fake, sessions, owner):
    runtime, engine, agent = await ready(world, fake, sessions)
    answer = await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": "Urgent"}, {"gmail": {ACCOUNT: {"percent": 0}}})
    assert answer["code"] == "share_invalid" and fake.executes == []


async def test_read_level_cannot_label_and_labels_level_cannot_send_or_draft(world, fake, sessions):
    runtime, engine, agent = await ready(world, fake, sessions, level="read")
    seen = {}

    async def handler(ctx, sink, gate):
        seen["read"] = await ask(ctx, gate, APPLY, {"thread_id": "th7", "label": "Urgent"})
        return TurnOutcome("succeeded", result="ok")

    await one_turn(world, handler)
    assert "decision" in seen["read"] and "result" not in seen["read"] and fake.executes == []     # denied at the gate

    await grant(sessions, agent, {}, level="labels")
    seen.clear()

    async def labels_handler(ctx, sink, gate):
        seen["tools"] = {t["name"] for t in composio_tools.mcp_tools(ctx.composio_apps)}
        seen["send"] = await ask(ctx, gate, "gmail_send_email", {"recipient_email": "c@example.com", "subject": "Hi", "body": "Hello"})
        seen["draft"] = await ask(ctx, gate, "gmail_create_draft_reply", {"thread_id": "th7", "recipient_email": "c@example.com", "subject": "Re", "body": "Hello"})
        return TurnOutcome("succeeded", result="ok")

    await one_turn(world, labels_handler)
    assert APPLY in seen["tools"] and "gmail_send_email" not in seen["tools"] and "gmail_create_draft_reply" not in seen["tools"]
    assert "decision" in seen["send"] and "decision" in seen["draft"] and fake.executes == []


async def test_label_call_is_allowed_without_a_card_and_recorded(world, fake, sessions):
    runtime, engine, agent = await ready(world, fake, sessions)
    seen = {}

    async def handler(ctx, sink, gate):
        seen["out"] = await card(runtime, ctx, gate, APPLY, {"thread_id": "th7", "label": "Urgent"})
        return TurnOutcome("succeeded", result="ok")

    await one_turn(world, handler)
    assert seen["out"]["status_when_asked"] != "pending"                       # no card: the level is the grant
    assert seen["out"]["answer"]["result"] == {"thread_id": "th7", "label": "Worker/Urgent", "applied": True}
    (row,) = await actions(sessions)
    assert (row.status, row.thread_id, row.label_name) == ("applied", "th7", "Worker/Urgent")


def test_plan_mode_denies_labelling_and_other_modes_allow_it_without_a_card():
    name = composio_policy.PREFIX + APPLY
    levels = {"gmail": "labels"}
    assert composio_policy.automatic(name, "plan", levels, {}) == ("denied", "plan_mode")
    assert composio_policy.automatic(name, "ask", levels, {}) == ("allowed", "composio_label_grant")
    assert composio_policy.automatic(name, "accept_edits", {"gmail": "write"}, {}) == ("allowed", "composio_label_grant")
    assert composio_policy.automatic(name, "ask", {"gmail": "read"}, {}) == ("denied", "write_not_allowed")
    assert composio_policy.automatic(name, "ask", levels, {}, uninterrupted=True) == ("allowed", "routine_uninterrupted_app")
    assert composio_policy.automatic(composio_policy.PREFIX + LIST, "plan", {"gmail": "read"}, {}) == ("allowed", "composio_read")


async def test_lowering_the_level_mid_turn_stops_later_calls(world, fake, sessions):
    runtime, engine, agent = await ready(world, fake, sessions)
    seen = {}

    async def handler(ctx, sink, gate):
        seen["first"] = await ask(ctx, gate, APPLY, {"thread_id": "th7", "label": "Urgent"})
        await grant(sessions, agent, {}, level="read")
        seen["second"] = await ask(ctx, gate, APPLY, {"thread_id": "th8", "label": "Urgent"})
        return TurnOutcome("succeeded", result="ok")

    await one_turn(world, handler)
    assert seen["first"]["result"]["applied"] is True
    assert seen["second"]["code"] == "not_allowed"
    assert [b["thread_id"] for b in modify_bodies(fake)] == ["th7"]


# -- the record, the caps, an unknown outcome ----------------------------------------------------------------------------

async def test_each_applied_label_is_recorded_with_run_and_account(world, fake, sessions, owner):
    runtime, engine, agent = await ready(world, fake, sessions)
    await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": "Urgent"}, turn=55, run=9)
    await direct(sessions, owner, agent, APPLY, {"thread_id": "th8", "label": "Bug"}, turn=55, run=9)
    rows = await actions(sessions)
    assert [(r.thread_id, r.label_name, r.status) for r in rows] == [("th7", "Worker/Urgent", "applied"), ("th8", "Worker/Bug", "applied")]
    assert {(r.owner_id, r.agent_id, r.account_id, r.turn_id, r.run_id) for r in rows} == {(owner, agent.id, ACCOUNT, 55, 9)}
    labels = {row.name: row.gmail_label_id for row in await recorded_labels(sessions)}
    assert {r.label_name: r.gmail_label_id for r in rows} == labels and all(r.applied_at and r.undone_at is None for r in rows)


async def test_an_identical_request_in_one_turn_is_answered_from_the_record(world, fake, sessions, owner):
    runtime, engine, agent = await ready(world, fake, sessions)
    first = await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": "Urgent"}, turn=5)
    again = await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": "Urgent"}, turn=5)
    assert first["result"]["applied"] and again["result"]["repeated"] is True
    assert len(modify_bodies(fake)) == 1 and len(await actions(sessions)) == 1
    await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": "Urgent"}, turn=6)      # a later turn is a new application
    assert len(modify_bodies(fake)) == 2 and len(await actions(sessions)) == 2


async def test_per_run_and_per_thread_caps(world, fake, sessions, owner, monkeypatch):
    runtime, engine, agent = await ready(world, fake, sessions)
    monkeypatch.setattr(worker_label_ops, "MAX_LABELS_PER_THREAD", 2)
    kinds = [(await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": word}, turn=1)).get("code")
             for word in ("Urgent", "Angry", "Bug")]
    assert kinds == [None, None, "label_limit"]                                            # a third distinct label on one thread
    again = await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": "Urgent"}, turn=2)
    assert again["result"]["applied"] is True                                              # a label it already has is not a new one
    monkeypatch.setattr(worker_label_ops, "MAX_APPLICATIONS_PER_TURN", 3)
    codes = [(await direct(sessions, owner, agent, APPLY, {"thread_id": f"th{n}", "label": "Question"}, turn=3)).get("code") for n in range(5)]
    assert codes == [None, None, None, "label_limit", "label_limit"]


def test_the_real_limits_are_a_hundred_a_run_and_the_whole_vocabulary_a_thread():
    assert worker_label_ops.MAX_APPLICATIONS_PER_TURN == 100 and worker_label_ops.MAX_LABELS_PER_THREAD == 8
    assert len(composio_tools.WORKER_LABELS) == 8 and len(set(composio_tools.WORKER_LABELS)) == 8


async def test_an_unknown_outcome_is_not_repeated(world, fake, sessions, owner):
    runtime, engine, agent = await ready(world, fake, sessions)
    fake.modify_down = True
    first = await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": "Urgent"}, turn=5)
    assert first["code"] == "outcome_unknown"
    (row,) = await actions(sessions)
    assert row.status == "outcome_unknown"
    fake.modify_down = False
    attempts = len(modify_bodies(fake))
    again = await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": "Urgent"}, turn=5)
    assert again["code"] == "outcome_unknown" and len(modify_bodies(fake)) == attempts      # never sent again in this turn
    assert [r.status for r in await actions(sessions)] == ["outcome_unknown"]


async def test_a_refusal_by_the_app_is_recorded_as_failed(world, fake, sessions, owner):
    runtime, engine, agent = await ready(world, fake, sessions)
    fake.modify_rejected = True
    answer = await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": "Urgent"}, turn=5)
    assert answer["code"] == "app_error"
    (row,) = await actions(sessions)
    assert (row.status, row.code) == ("failed", "app_error")


async def test_listing_shows_only_our_labels_and_flags_a_look_alike(world, fake, sessions, owner):
    runtime, engine, agent = await ready(world, fake, sessions)
    await direct(sessions, owner, agent, APPLY, {"thread_id": "th7", "label": "Urgent"})
    fake.labels.append({"id": "Label_theirs", "name": "Worker/Bug", "type": "user"})     # exists, not made by this app
    fake.labels.append({"id": "Label_misc", "name": "Worker/Something else", "type": "user"})   # an owner's own label: never named
    answer = await direct(sessions, owner, agent, LIST, {})
    assert [item["name"] for item in answer["result"]] == ["Worker/Urgent"] and answer["not_ours"] == ["Worker/Bug"]
    assert "Customers" not in json.dumps(answer) and "Something else" not in json.dumps(answer) and "INBOX" not in json.dumps(answer)


# -- a scheduled routine -------------------------------------------------------------------------------------------------

class _Runtime:
    def __init__(self, sessions, owner):
        self.session, self.owner_id = sessions, owner


@pytest.fixture
async def routine_world(sessions, owner, monkeypatch, fake):
    fake.accounts.append(gmail_account())
    grants = [{"tool": APPLY, "account_id": ACCOUNT}]
    async with sessions() as db:
        agent = await agent_store.create_agent(db, owner_id=owner, name="Routine labeller")
        await agent_store.update_agent(db, agent.id, connector_scopes={"gmail": "labels"}, connector_account_scopes={"gmail": [ACCOUNT]})
        await composio_service._save(db, owner, {"api_key": KEY, "user_id": "u1", "session_id": "sess_1"})
        routine = Routine(owner_id=owner, agent_id=agent.id, name="Shadow", prompt="Label", schedule_kind="manual", schedule_spec={},
                          timezone="UTC", paused=False, run_limit_minutes=30, approval_mode="ask", approval_timeout_minutes=30,
                          catch_up_minutes=60, retry_limit=0, retry_delay_minutes=5, connected_app_grants=grants)
        db.add(routine)
        await db.commit()
        await db.refresh(routine)
        routine_run = RoutineRun(routine_id=routine.id, trigger="manual", state="running", prompt="Label", thread_id=811, connected_app_grants=grants)
        db.add(routine_run)
        await db.commit()
        await db.refresh(routine_run)
    runtime = _Runtime(sessions, owner)

    async def approved(*_args):
        return True
    monkeypatch.setattr("app.team.bridge.consume_allowed_approval", approved)

    def bridge_for(tool_grants, level="labels"):
        run = SimpleNamespace(ctx=SimpleNamespace(aborted=False, computer=None, approval_mode="ask", composio_account_scopes={"gmail": [ACCOUNT]}),
                              routine_grants=tool_grants, routine_id=routine.id, routine_run_id=routine_run.id, thread_id=811,
                              agent_id=agent.id, turn_id=77, owner_token="routine-owner")
        return composio_bridge.ComposioBridge(runtime, run, {"gmail": level}, account_scopes={"gmail": [ACCOUNT]})

    yield bridge_for, routine_run
    async with sessions() as db:
        await db.execute(delete(WorkerLabelAction).where(WorkerLabelAction.owner_id == owner))
        await db.execute(delete(WorkerLabel).where(WorkerLabel.owner_id == owner))
        for run in list(await db.scalars(select(RoutineRun).where(RoutineRun.routine_id == routine_run.routine_id))):
            await db.delete(run)
        await db.flush()
        await db.execute(delete(Routine).where(Routine.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


async def test_a_routine_with_the_tool_in_its_grants_can_label_and_one_without_cannot(routine_world, fake, sessions):
    bridge_for, routine_run = routine_world
    first = await bridge_for({APPLY: ACCOUNT}).execute(APPLY, {"thread_id": "th7", "label": "Urgent"})
    assert first["result"] == {"thread_id": "th7", "label": "Worker/Urgent", "applied": True}
    (row,) = await actions(sessions)
    assert (row.turn_id, row.run_id, row.account_id, row.status) == (77, routine_run.id, ACCOUNT, "applied")
    async with sessions() as db:
        receipts = list((await db.execute(select(RoutineAppAction).where(RoutineAppAction.run_id == routine_run.id))).scalars())
    assert [(r.tool, r.account_id, r.status) for r in receipts] == [(APPLY, ACCOUNT, "completed")]
    # The same request again in this run is refused by the receipt before it reaches Gmail.
    again = await bridge_for({APPLY: ACCOUNT}).execute(APPLY, {"thread_id": "th7", "label": "Urgent"})
    assert again["code"] == "already_attempted" and len(modify_bodies(fake)) == 1
    # A routine whose grants do not name the tool cannot label, and a labels-level routine cannot send.
    other = await bridge_for({"gmail_get_profile": ACCOUNT}).execute(APPLY, {"thread_id": "th8", "label": "Urgent"})
    assert other["code"] == "not_allowed" and len(modify_bodies(fake)) == 1
    send = await bridge_for({"gmail_send_email": ACCOUNT}).execute("gmail_send_email", {"recipient_email": "c@example.com", "subject": "Hi", "body": "Hello"})
    assert send["code"] == "not_allowed"
    # At the read level the grant alone is not enough.
    low = await bridge_for({APPLY: ACCOUNT}, level="read").execute(APPLY, {"thread_id": "th9", "label": "Urgent"})
    assert low["code"] == "not_allowed" and len(modify_bodies(fake)) == 1
