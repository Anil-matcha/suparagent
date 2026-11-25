"""request_app_connection: any agent asks for an app from the chat; it records a card and connects nothing."""
import pytest
from sqlalchemy import select
from app.db.models import Agent, Event
from app.services import composio_service
from app.services.composio_client import Account
from app.services.provider_adapter import TurnOutcome
from app.team import connections, policy, service
from tests.team.test_collaboration import harness  # noqa: F401
from tests.services.thread_harness import sessions, owner, cleanup, wait_until  # noqa: F401


def account(app, status="ACTIVE"):
    return Account(id=f"acc-{app}", toolkit=app, alias=None, status=status, created_at=None, updated_at=None)


async def ask(harness, monkeypatch, *, configured=True, accounts=(), app="gmail", scopes=None, args=None):
    rt, engine, lead, worker = harness
    async def is_configured(db, owner_id):
        return configured
    async def listed(db, owner_id, make_client):
        return list(accounts)
    monkeypatch.setattr(composio_service, "configured", is_configured)
    monkeypatch.setattr(composio_service, "accounts", listed)
    if scopes is not None:
        async with rt.session() as db:
            (await db.get(Agent, worker.id)).connector_scopes = scopes
            await db.commit()
    thread = await rt.create_thread(agent_id=worker.id)
    seen = {}
    async def handler(ctx, sink, gate):
        try:
            seen["result"] = await connections.call(rt, rt._runs[ctx.turn_id], "request_app_connection",
                                                   args if args is not None else {"app": app, "reason": "Rank the support inbox"})
        except service.TeamError as exc:
            seen["error"] = exc.code
        return TurnOutcome("succeeded", result="done")
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, "Rank my open threads")
    await wait_until(lambda: turn.id not in rt._runs)
    async with rt.session() as db:
        events = list((await db.execute(select(Event).where(Event.thread_id == thread.id, Event.type == connections.EVENT))).scalars())
    return seen, events, worker


async def test_not_set_up_says_needs_key(harness, monkeypatch):
    seen, events, _ = await ask(harness, monkeypatch, configured=False)
    assert seen["result"]["state"] == "needs_key" and "Never ask for a key" in seen["result"]["next"]
    assert [e.data["state"] for e in events] == ["needs_key"]


async def test_no_account_says_needs_connection_and_records_one_card(harness, monkeypatch):
    seen, events, worker = await ask(harness, monkeypatch, accounts=[account("github")])
    assert seen["result"]["state"] == "needs_connection"
    (event,) = events
    assert event.data["app"] == "gmail" and event.data["agent_id"] == worker.id
    assert event.data["request_id"] == seen["result"]["request_id"] and event.data["reason"] == "Rank the support inbox"


async def test_connected_but_not_allowed_says_needs_access(harness, monkeypatch):
    seen, _, _ = await ask(harness, monkeypatch, accounts=[account("gmail")], scopes={"github": "read"})
    assert seen["result"]["state"] == "needs_access" and "cannot grant yourself" in seen["result"]["next"]


async def test_connected_and_allowed_is_ready(harness, monkeypatch):
    seen, _, _ = await ask(harness, monkeypatch, accounts=[account("gmail")], scopes={"gmail": "read"})
    assert seen["result"]["state"] == "ready"


async def test_a_failed_account_does_not_count_as_connected(harness, monkeypatch):
    seen, _, _ = await ask(harness, monkeypatch, accounts=[account("gmail", "FAILED")])
    assert seen["result"]["state"] == "needs_connection"


@pytest.mark.parametrize("args", [{"app": "../etc"}, {"app": 5}, {"app": "gmail", "token": "x"}, {"app": "gmail", "reason": "x" * 201}])
async def test_bad_arguments_are_refused_and_record_nothing(harness, monkeypatch, args):
    seen, events, _ = await ask(harness, monkeypatch, args=args)
    assert seen["error"] in {"invalid_input", "unsupported_app"} and events == []


def team(origin):
    return policy.TurnTeam(team_id=1, agent_id=2, is_lead=False, lead_auto=False, origin=origin)


def test_policy_allows_a_member_in_a_person_chat_and_denies_unattended_runs():
    assert policy.automatic("mcp__team__request_app_connection", "ask", team("person"), {}) == ("allowed", "team_connect_request")
    for origin in policy.UNATTENDED:
        assert policy.automatic("mcp__team__request_app_connection", "ask", team(origin), {}) == ("denied", "outside_origin")
