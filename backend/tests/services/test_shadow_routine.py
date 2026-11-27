"""A scheduled or new-mail shadow run (BL-173 slice 5): a routine that reads mail, records how it ranks it and adds its own
labels, unattended, and can do nothing else. Real bridge, real routine receipts, the paging Gmail fake with labels."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app import composio_tools, gmail_share
from app.db.models import Agent, ShadowPrediction
from app.db.models.routine import Routine, RoutineAppAction, RoutineRun
from app.db.models.user_credential import UserCredential
from app.schemas.routine import ConnectedAppGrant, is_shadow, validate_shadow_grants
from app.services import agent_store, composio_bridge, composio_service, routine_uninterrupted, tool_policy
from app.composio_mcp_server import Server
from tests.services.test_composio_bridge import KEY, world as composio_world  # noqa: F401
from tests.services.test_composio_gmail_share import ACCOUNT, entry, gmail_account, rule, thread_in
from tests.services.test_routine_app_runtime import _remove_runtime_routine, _runtime_mount_context
from tests.services.test_worker_labels import (  # noqa: F401
    LabelFake, _Runtime, actions, clean_label_tables, fake, modify_bodies)
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401

MARK, LIST, APPLY = composio_tools.TRIAGE_MARK, composio_tools.LABEL_LIST, composio_tools.LABEL_APPLY
READS = ["gmail_get_profile", "gmail_fetch_emails", "gmail_fetch_thread", "gmail_fetch_message"]
SHADOW = READS + [MARK, LIST, APPLY]


def grants_for(tools, account=ACCOUNT):
    return [{"tool": tool, "account_id": account} for tool in tools]


# -- the rule: what a shadow routine may hold -----------------------------------------------------------------------------

def parsed(tools, account=ACCOUNT):
    return [ConnectedAppGrant(**g) for g in grants_for(tools, account)]


def test_a_routine_is_a_shadow_run_when_it_marks_or_labels():
    assert is_shadow(parsed([MARK])) and is_shadow(parsed([APPLY])) and is_shadow(parsed(SHADOW))
    assert not is_shadow(parsed(READS)) and not is_shadow(parsed([LIST])) and not is_shadow([])


def test_the_full_shadow_set_is_accepted():
    validate_shadow_grants(parsed(SHADOW))
    validate_shadow_grants(parsed([MARK]))
    validate_shadow_grants(parsed([APPLY, "gmail_fetch_thread"]))
    validate_shadow_grants(parsed(READS))  # not a shadow run at all: nothing to add


@pytest.mark.parametrize("extra", ["gmail_send_email", "gmail_create_draft_reply", "gmail_prepare_checked_reply",
                                   "github_get_authenticated_user", "github_create_branch", "googlecalendar_list_calendars"])
def test_a_shadow_run_holds_no_other_change_and_no_other_app(extra):
    for tools in (SHADOW + [extra], [MARK, extra]):
        try:
            grants = parsed(tools)
        except ValueError:
            continue   # a tool no routine may hold at all is refused earlier, by the grant itself
        with pytest.raises(ValueError, match="shadow run"):
            validate_shadow_grants(grants)


def test_a_shadow_run_works_in_one_mailbox():
    mixed = parsed(SHADOW[:3]) + [ConnectedAppGrant(tool=APPLY, account_id="ca_other")]
    with pytest.raises(ValueError, match="one Gmail account"):
        validate_shadow_grants(mixed)


# -- the run itself ------------------------------------------------------------------------------------------------------

@pytest.fixture
async def shadow_world(sessions, owner, monkeypatch, fake):
    fake.accounts.append(gmail_account())
    snapshot = grants_for(SHADOW)
    async with sessions() as db:
        agent = await agent_store.create_agent(db, owner_id=owner, name="Shadow worker")
        await agent_store.update_agent(db, agent.id, connector_scopes={"gmail": "labels"}, connector_account_scopes={"gmail": [ACCOUNT]})
        row = await db.get(Agent, agent.id)
        row.connector_shares = {"gmail": {ACCOUNT: entry(50)}}
        await composio_service._save(db, owner, {"api_key": KEY, "user_id": "u1", "session_id": "sess_1"})
        routine = Routine(owner_id=owner, agent_id=agent.id, name="Shadow", prompt="Rank and label", schedule_kind="manual", schedule_spec={},
                          timezone="UTC", paused=False, run_limit_minutes=30, approval_mode="ask", approval_timeout_minutes=30,
                          catch_up_minutes=60, retry_limit=0, retry_delay_minutes=5, connected_app_grants=snapshot)
        db.add(routine)
        await db.commit()
        await db.refresh(routine)
        routine_run = RoutineRun(routine_id=routine.id, trigger="manual", state="running", prompt="Rank", thread_id=911, connected_app_grants=snapshot)
        db.add(routine_run)
        await db.commit()
        await db.refresh(routine_run)
    runtime = _Runtime(sessions, owner)

    async def approved(*_args):
        return True
    monkeypatch.setattr("app.team.bridge.consume_allowed_approval", approved)

    def bridge_for(tools=SHADOW, level="labels", turn=88):
        run = SimpleNamespace(ctx=SimpleNamespace(aborted=False, computer=None, approval_mode="ask", composio_account_scopes={"gmail": [ACCOUNT]}),
                              routine_grants={t: ACCOUNT for t in tools}, routine_id=routine.id, routine_run_id=routine_run.id, thread_id=911,
                              agent_id=agent.id, turn_id=turn, owner_token="routine-owner")
        return composio_bridge.ComposioBridge(runtime, run, {"gmail": level}, account_scopes={"gmail": [ACCOUNT]})

    async def set_level(level, shares=None):
        async with sessions() as db:
            await agent_store.update_agent(db, agent.id, connector_scopes={"gmail": level})
            if shares is not None:
                (await db.get(Agent, agent.id)).connector_shares = shares
            await db.commit()

    yield bridge_for, routine_run, set_level
    async with sessions() as db:
        await db.execute(delete(ShadowPrediction))
        await db.execute(delete(RoutineAppAction))
        for run in list(await db.scalars(select(RoutineRun).where(RoutineRun.routine_id == routine_run.routine_id))):
            await db.delete(run)
        await db.flush()
        await db.execute(delete(Routine).where(Routine.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


async def predictions(sessions):
    async with sessions() as db:
        return list((await db.execute(select(ShadowPrediction).order_by(ShadowPrediction.id))).scalars())


async def receipts(sessions, run_id):
    async with sessions() as db:
        return list((await db.execute(select(RoutineAppAction).where(RoutineAppAction.run_id == run_id).order_by(RoutineAppAction.id))).scalars())


async def test_a_scheduled_shadow_run_reads_in_share_records_and_labels_once(shadow_world, fake, sessions):
    bridge_for, routine_run, _ = shadow_world
    r = rule(50)
    bridge = bridge_for()
    found = await bridge.execute("gmail_fetch_emails", {"max_results": 30})
    threads = [row["threadId"] for row in found["result"]]
    assert threads and all(gmail_share.in_share(r, t) for t in threads)   # only the agent's share was ever shown
    chosen = threads[0]
    mark = await bridge.execute(MARK, {"thread_id": chosen, "rank": 1, "category": "urgent"})
    assert mark == {"recorded": True, "thread_id": chosen, "rank": 1}
    label = await bridge.execute(APPLY, {"thread_id": chosen, "label": "Urgent"})
    assert label["result"] == {"thread_id": chosen, "label": "Worker/Urgent", "applied": True}
    # the mark is the app's own record (no Gmail call), the label is one add and no remove
    assert not [e for e in fake.executes if e.get("slug") == "GMAIL_TRIAGE_MARK"]
    assert [b.get("remove_label_ids") for b in modify_bodies(fake)] == [None] or all(not b.get("remove_label_ids") for b in modify_bodies(fake))
    assert len(modify_bodies(fake)) == 1
    (row,) = await predictions(sessions)
    assert (row.thread_id, row.account_id, row.turn_id, row.rank) == (chosen, ACCOUNT, 88, 1)
    (action,) = await actions(sessions)
    assert (action.thread_id, action.run_id, action.turn_id, action.status, action.account_id) == (chosen, routine_run.id, 88, "applied", ACCOUNT)
    by_tool = {r_.tool: r_.status for r_ in await receipts(sessions, routine_run.id)}
    assert by_tool[APPLY] == "completed" and by_tool["gmail_fetch_emails"] == "completed" and by_tool[MARK] == "completed"
    # a repeated label in the same run is not repeated; a repeated mark replaces itself
    again = await bridge.execute(APPLY, {"thread_id": chosen, "label": "Urgent"})
    assert again["code"] == "already_attempted" and len(modify_bodies(fake)) == 1
    await bridge.execute(MARK, {"thread_id": chosen, "rank": 2, "category": "bug"})
    (row,) = await predictions(sessions)
    assert (row.rank, row.category) == (2, "bug")


async def test_a_shadow_run_is_refused_everything_outside_its_grants_even_if_asked(shadow_world, fake):
    bridge_for, _, _ = shadow_world
    bridge = bridge_for()
    before = len(fake.executes)
    asked = [
        ("gmail_send_email", {"recipient_email": "c@example.com", "subject": "Hi", "body": "Hello"}),
        ("gmail_create_draft_reply", {"thread_id": "th1", "recipient_email": "c@example.com", "subject": "Re", "body": "Hello"}),
        ("gmail_prepare_checked_reply", {"message_id": "m1", "thread_id": "th1", "subject": "Re", "body": "Hello"}),
        ("github_get_authenticated_user", {}),
        ("app_run_action", {"app": "notion", "action": "NOTION_CREATE_PAGE", "arguments": {}}),
        ("app_search_actions", {"app": "gmail", "query": "delete"}),
        ("not_a_real_tool", {}),
    ]
    for name, args in asked:
        answer = await bridge.execute(name, args)
        assert answer.get("code") in {"not_allowed", "bad_arguments", "use_curated"}, (name, answer)
    assert len(fake.executes) == before      # nothing reached Gmail or Composio
    # and the approval gate decides the same without any card, for every one of them
    run = SimpleNamespace(routine_grants={t: ACCOUNT for t in SHADOW}, ctx=SimpleNamespace(composio_apps={"gmail": "labels"}, team=None))
    for name, args in asked:
        assert tool_policy.automatic(composio_bridge.PREFIX + name, "ask", run, args) == ("denied", "routine_action_not_granted"), name


async def test_the_policy_allows_exactly_the_granted_shadow_tools(shadow_world):
    run = SimpleNamespace(routine_grants={t: ACCOUNT for t in SHADOW}, ctx=SimpleNamespace(composio_apps={"gmail": "labels"}, team=None))
    for name in READS + [MARK, LIST]:
        assert tool_policy.automatic(composio_bridge.PREFIX + name, "ask", run, {})[0] == "allowed", name
    assert tool_policy.automatic(composio_bridge.PREFIX + APPLY, "ask", run, {}) == ("allowed", "routine_label_grant")
    assert tool_policy.automatic(composio_bridge.PREFIX + APPLY, "plan", run, {}) == ("denied", "plan_mode")


async def test_the_agents_level_is_a_ceiling_for_the_routine(shadow_world, fake, sessions):
    bridge_for, _, set_level = shadow_world
    await set_level("read")
    bridge = bridge_for(level="read")
    allowed = await bridge.execute(MARK, {"thread_id": thread_in(rule(50)), "rank": 1, "category": "urgent"})
    assert allowed["recorded"] is True                                   # recording is a read
    denied = await bridge.execute(APPLY, {"thread_id": thread_in(rule(50)), "label": "Urgent"})
    assert denied["code"] == "not_allowed" and not modify_bodies(fake)   # labelling needs the labels level


async def test_the_share_applies_to_a_shadow_run(shadow_world, fake, sessions):
    bridge_for, _, _ = shadow_world
    bridge = bridge_for()
    out = thread_in(rule(50), False)
    before = len(fake.executes)
    assert (await bridge.execute(MARK, {"thread_id": out, "rank": 1, "category": "urgent"}))["code"] == "outside_share"
    assert (await bridge.execute(APPLY, {"thread_id": out, "label": "Urgent"}))["code"] == "outside_share"
    assert (await bridge.execute("gmail_fetch_thread", {"thread_id": out}))["code"] == "outside_share"
    assert len(fake.executes) == before and not await predictions(sessions)


async def test_revoking_the_level_mid_run_stops_the_next_label(shadow_world, fake, sessions):
    bridge_for, _, set_level = shadow_world
    bridge = bridge_for()
    inside = [thread_in(rule(50)), thread_in(rule(50), True, 200)]
    first = await bridge.execute(APPLY, {"thread_id": inside[0], "label": "Urgent"})
    assert first["result"]["applied"] is True
    await set_level("read")
    second = await bridge.execute(APPLY, {"thread_id": inside[1], "label": "Bug"})
    assert second["code"] == "not_allowed" and len(modify_bodies(fake)) == 1


async def test_a_grant_the_current_routine_no_longer_holds_is_refused(shadow_world, fake, sessions):
    bridge_for, routine_run, _ = shadow_world
    async with sessions() as db:
        routine = await db.get(Routine, routine_run.routine_id)
        routine.connected_app_grants = grants_for([t for t in SHADOW if t != APPLY])
        await db.commit()
    answer = await bridge_for().execute(APPLY, {"thread_id": thread_in(rule(50)), "label": "Urgent"})
    assert answer["code"] == "not_allowed" and not modify_bodies(fake)


# -- the real mount and the uninterrupted ceiling ---------------------------------------------------------------------

@pytest.mark.parametrize("level,mounted,tools", [
    ("labels", {"gmail": "labels"}, SHADOW),
    ("read", {"gmail": "read"}, READS + [MARK, LIST]),   # the label grant is dropped below the labels level
    ("write", {"gmail": "labels"}, SHADOW),   # the routine's own grants name no write tool, so even a write-level agent's shadow run mounts at labels
])
async def test_the_runtime_mounts_a_shadow_routine_at_the_agents_level(composio_world, sessions, owner, level, mounted, tools):
    runtime, engine, _, routine, _, _, run = await _runtime_mount_context(
        composio_world, sessions, owner, scopes={"gmail": level}, grants=grants_for(SHADOW, "ca_mail"))
    try:
        assert await runtime._composio_apps(run, engine) == mounted
        assert set(run.routine_grants) == set(tools)
        bridge = composio_bridge.ComposioBridge(runtime, run, mounted)
        try:
            args = bridge.launch.args
            assert args[args.index("--apps") + 1] == f"gmail={mounted['gmail']}"
            exact = args[args.index("--exact-tools") + 1].split(",")
            assert set(exact) == set(tools) and not {"gmail_send_email", "gmail_create_draft_reply"} & set(exact) - set(SHADOW)
            server = Server(bridge.folder, mounted, set(exact))
            assert {t["name"] for t in server.tools} == set(tools) | {"read_saved_result"}
        finally:
            await bridge.close()
    finally:
        await _remove_runtime_routine(sessions, routine.id)


async def test_a_grant_for_an_account_the_agent_does_not_have_is_not_mounted(composio_world, sessions, owner):
    runtime, engine, _, routine, _, _, run = await _runtime_mount_context(
        composio_world, sessions, owner, scopes={"gmail": "labels"}, grants=grants_for(SHADOW, "ca_not_the_agents"))
    try:
        assert await runtime._composio_apps(run, engine) == {} and run.routine_grants == {}
    finally:
        await _remove_runtime_routine(sessions, routine.id)


@pytest.mark.parametrize("level,mark,label,send", [("read", True, False, False), ("labels", True, True, False), ("write", True, True, True)])
async def test_the_uninterrupted_ceiling_follows_the_agents_level(shadow_world, sessions, owner, level, mark, label, send):
    _, _, set_level = shadow_world
    await set_level(level)
    async with sessions() as db:
        agent = (await db.execute(select(Agent).where(Agent.owner_id == owner))).scalars().first()
        ceiling = await routine_uninterrupted.snapshot(db, owner, agent.id)
    names = set(ceiling["composio_tools"])
    assert (MARK in names, APPLY in names, "gmail_send_email" in names) == (mark, label, send)
