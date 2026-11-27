"""gmail_triage_mark (BL-173): a worker records how it ranked a thread; app database only, never Gmail."""
import hashlib

import pytest
from sqlalchemy import delete, select

from app import composio_tools, gmail_share
from app.db.models import Agent, ShadowPrediction
from app.db.models.user_credential import UserCredential
from app.services import agent_store, composio_policy, shadow_marks
from app.services.provider_adapter import TurnOutcome
from tests.services.test_composio_bridge import card, one_turn, world  # noqa: F401
from tests.services.test_composio_gmail_share import (  # noqa: F401
    ACCOUNT, GmailFake, entry, fake, gmail_account, grant, rule, thread_in)
from tests.services.thread_harness import cleanup, owner, sessions, wait_until  # noqa: F401

MARK = composio_tools.TRIAGE_MARK


async def marks(sessions):
    async with sessions() as db:
        return list((await db.execute(select(ShadowPrediction).order_by(ShadowPrediction.id))).scalars())


async def run_marks(world, calls, *, decide=True):
    """One turn in which the agent makes each call in order; returns every answer and the card state of the first."""
    seen = {"answers": [], "asked": None}

    async def handler(ctx, sink, gate):
        for index, args in enumerate(calls):
            out = await card(world[0], ctx, gate, MARK, args)
            if index == 0:
                seen["asked"] = out["status_when_asked"]
            seen["answers"].append(out.get("answer", out))
        return TurnOutcome("succeeded", result="ok")

    await one_turn(world, handler)
    return seen


def call(thread="th5", **more):
    return {"thread_id": thread, "rank": 1, "category": "urgent", **more}


async def test_mark_records_a_prediction_and_touches_gmail_never(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, {})
    seen = await run_marks(world, [call(owner_only=True)])
    assert seen["answers"] == [{"recorded": True, "thread_id": "th5", "rank": 1}]
    assert fake.executes == []  # no Composio execute at all
    (row,) = await marks(sessions)
    assert (row.agent_id, row.account_id, row.thread_id, row.rank, row.category, row.owner_only) == (agent.id, ACCOUNT, "th5", 1, "urgent", True)
    assert row.turn_id is not None and row.draft_text is None and row.draft_digest is None


async def test_mark_at_read_level_is_allowed_without_a_card(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, {}, level="read")
    seen = await run_marks(world, [call()])
    assert seen["asked"] != "pending" and seen["answers"][0]["recorded"] is True


async def test_mark_outside_the_share_is_refused_and_nothing_is_written(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, {"gmail": {ACCOUNT: entry(5)}})
    out = thread_in(rule(5), False)
    seen = await run_marks(world, [call(out), call(thread_in(rule(5)))])
    assert seen["answers"][0]["code"] == "outside_share"
    assert seen["answers"][1]["recorded"] is True
    assert [row.thread_id for row in await marks(sessions)] == [thread_in(rule(5))]
    assert fake.executes == []


async def test_a_damaged_share_rule_denies_the_mark(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, {"gmail": {ACCOUNT: {"percent": 0}}})
    seen = await run_marks(world, [call()])
    assert seen["answers"][0]["code"] == "share_invalid" and await marks(sessions) == []


async def test_a_repeat_in_one_turn_replaces_and_a_new_turn_adds_a_row(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, {})
    await run_marks(world, [call(rank=3), call(rank=1, category="angry")])
    (row,) = await marks(sessions)
    assert (row.rank, row.category) == (1, "angry")
    await run_marks(world, [call(rank=2)])
    assert [r.rank for r in await marks(sessions)] == [1, 2]
    assert len({r.turn_id for r in await marks(sessions)}) == 2


async def test_two_gmail_accounts_need_the_account_name(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts += [{**gmail_account("ca_g1"), "alias": "support"}, {**gmail_account("ca_g2"), "alias": "sales"}]
    await grant(sessions, agent, {}, accounts=("ca_g1", "ca_g2"))
    seen = await run_marks(world, [call(), call(account="support")])
    assert seen["answers"][0]["code"] == "account_required"
    assert seen["answers"][1]["recorded"] is True
    assert [r.account_id for r in await marks(sessions)] == ["ca_g1"]


async def test_the_draft_is_redacted_before_it_is_kept_and_the_digest_matches(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, {})
    text = ("Hi, sorry about order 123456789. Call +1 (555) 123-4567 or write jane.doe@example.com. "
            "See https://x.example/track?id=SECRET&t=1 and https://docs.example/help. Key sk-ABCDEF0123456789abcdef.")
    await run_marks(world, [call(draft_text=text, draft_ref="memory/drafts/th5.md")])
    (row,) = await marks(sessions)
    kept = row.draft_text
    for leaked in ("123456789", "555", "jane.doe@example.com", "id=SECRET", "sk-ABCDEF0123456789abcdef"):
        assert leaked not in kept
    assert "[number]" in kept and "[email]" in kept and "[link]" in kept
    assert "https://docs.example/help" in kept  # a plain link is not a secret
    assert row.draft_digest == hashlib.sha256(kept.encode()).hexdigest() and row.draft_ref == "memory/drafts/th5.md"


@pytest.mark.parametrize("bad", [
    {"rank": 0}, {"rank": 1001}, {"rank": True}, {"rank": "1"}, {"category": "angry!"}, {"category": "Urgent"},
    {"thread_id": "../x"}, {"thread_id": "a" * 201}, {"draft_text": "x" * 4001}, {"draft_text": ""}, {"draft_ref": "x" * 201},
    {"owner_only": "yes"}, {"agent_id": 9}, {"account_id": "ca_other"}, {"owner_id": "me"},
])
async def test_out_of_range_or_extra_arguments_are_refused(world, fake, sessions, bad):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, {})
    seen = await run_marks(world, [{**call(), **bad}])
    assert seen["answers"][0].get("code") == "bad_arguments" and await marks(sessions) == []


async def test_the_hundred_and_first_thread_in_a_turn_is_refused_but_a_replacement_is_not(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, {})
    calls = [call(f"th{n}", rank=n + 1) for n in range(shadow_marks.MAX_MARKS_PER_TURN)]
    calls += [call("th100", rank=101), call("th0", rank=500)]
    seen = await run_marks(world, calls)
    assert seen["answers"][-2]["code"] == "mark_limit"
    assert seen["answers"][-1]["recorded"] is True  # th0 again: replaces its own row
    rows = await marks(sessions)
    assert len(rows) == shadow_marks.MAX_MARKS_PER_TURN and next(r for r in rows if r.thread_id == "th0").rank == 500


def test_plan_mode_denies_the_mark_and_other_modes_allow_it_without_a_card():
    name = composio_policy.PREFIX + MARK
    assert composio_policy.automatic(name, "plan", {"gmail": "read"}, {}) == ("denied", "plan_mode")
    assert composio_policy.automatic(name, "ask", {"gmail": "read"}, {}) == ("allowed", "composio_read")
    assert composio_policy.automatic(name, "ask", {"github": "read"}, {}) == ("denied", "app_not_allowed")


def test_the_mark_is_a_read_tool_so_a_read_grant_lists_it_and_the_slug_is_never_a_real_action():
    tool = composio_tools.BY_NAME[MARK]
    assert tool.kind == "read" and tool.app == "gmail" and tool.special == "triage_mark"
    assert MARK in [t["name"] for t in composio_tools.mcp_tools({"gmail": "read"})]
    assert MARK not in [t["name"] for t in composio_tools.mcp_tools({"github": "read"})]


def test_redact_personal_alone():
    assert shadow_marks.redact_personal("call 0044 20 7946 0958 now") == "call [number] now"
    assert shadow_marks.redact_personal("room 12, floor 3") == "room 12, floor 3"  # short numbers stay
    assert shadow_marks.redact_personal("a@b.co and c.d+e@f.g.org") == "[email] and [email]"
    assert shadow_marks.redact_personal("https://a.b/c#frag") == "[link]" and shadow_marks.redact_personal("https://a.b/c") == "https://a.b/c"


def test_a_plain_date_is_not_mistaken_for_a_number():
    assert shadow_marks.redact_personal("by 2026-11-26 please") == "by 2026-11-26 please"
    assert shadow_marks.redact_personal("call +1 (555) 123-4567") == "call [number]"


# -- a scheduled routine -------------------------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

import httpx  # noqa: E402

from app.db.models.routine import Routine, RoutineAppAction, RoutineRun  # noqa: E402
from app.services import composio_bridge, composio_service  # noqa: E402
from app.services.composio_client import ComposioClient  # noqa: E402
from tests.services.test_composio_bridge import KEY  # noqa: E402


class _Runtime:
    def __init__(self, sessions, owner):
        self.session, self.owner_id = sessions, owner


@pytest.fixture
async def routine_world(sessions, owner, monkeypatch):
    fake = GmailFake()
    monkeypatch.setattr(composio_bridge, "make_client", lambda key: ComposioClient(key, transport=httpx.MockTransport(fake.handler)))
    fake.accounts.append(gmail_account())
    async with sessions() as db:
        agent = await agent_store.create_agent(db, owner_id=owner, name="Routine triager")
        await agent_store.update_agent(db, agent.id, connector_scopes={"gmail": "read"}, connector_account_scopes={"gmail": [ACCOUNT]})
        await composio_service._save(db, owner, {"api_key": KEY, "user_id": "u1", "session_id": "sess_1"})
        routine = Routine(owner_id=owner, agent_id=agent.id, name="Triage", prompt="Triage", schedule_kind="manual", schedule_spec={},
                          timezone="UTC", paused=False, run_limit_minutes=30, approval_mode="ask", approval_timeout_minutes=30,
                          catch_up_minutes=60, retry_limit=0, retry_delay_minutes=5,
                          connected_app_grants=[{"tool": MARK, "account_id": ACCOUNT}])
        db.add(routine)
        await db.commit()
        await db.refresh(routine)
        routine_run = RoutineRun(routine_id=routine.id, trigger="manual", state="running", prompt="Triage", thread_id=711,
                                 connected_app_grants=[{"tool": MARK, "account_id": ACCOUNT}])
        db.add(routine_run)
        await db.commit()
        await db.refresh(routine_run)
    runtime = _Runtime(sessions, owner)

    async def approved(*_args):
        return True
    monkeypatch.setattr("app.team.bridge.consume_allowed_approval", approved)

    def bridge_for(grants):
        run = SimpleNamespace(ctx=SimpleNamespace(aborted=False, computer=None, approval_mode="ask", composio_account_scopes={"gmail": [ACCOUNT]}),
                              routine_grants=grants, routine_id=routine.id, routine_run_id=routine_run.id, thread_id=711,
                              agent_id=agent.id, turn_id=41, owner_token="routine-owner")
        return composio_bridge.ComposioBridge(runtime, run, {"gmail": "read"}, account_scopes={"gmail": [ACCOUNT]})

    yield fake, bridge_for, routine_run
    async with sessions() as db:
        await db.execute(delete(ShadowPrediction).where(ShadowPrediction.owner_id == owner))
        for run in list(await db.scalars(select(RoutineRun).where(RoutineRun.routine_id == routine_run.routine_id))):
            await db.delete(run)
        await db.flush()
        await db.execute(delete(Routine).where(Routine.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


async def test_a_routine_granted_the_mark_records_it_with_a_receipt_and_no_composio_call(routine_world, sessions):
    fake, bridge_for, routine_run = routine_world
    answer = await bridge_for({MARK: ACCOUNT}).execute(MARK, call("th9", rank=2))
    assert answer == {"recorded": True, "thread_id": "th9", "rank": 2}
    assert fake.executes == []
    (row,) = await marks(sessions)
    assert (row.thread_id, row.turn_id, row.account_id) == ("th9", 41, ACCOUNT)
    async with sessions() as db:
        receipts = list((await db.execute(select(RoutineAppAction).where(RoutineAppAction.run_id == routine_run.id))).scalars())
    assert [(r.tool, r.account_id, r.status) for r in receipts] == [(MARK, ACCOUNT, "completed")]


async def test_a_routine_not_granted_the_mark_cannot_record(routine_world, sessions):
    fake, bridge_for, _ = routine_world
    answer = await bridge_for({"gmail_get_profile": ACCOUNT}).execute(MARK, call())
    assert answer["code"] == "not_allowed" and await marks(sessions) == [] and fake.executes == []
