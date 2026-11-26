"""request_app_connection: any agent asks for an app from the chat; it records a card and connects nothing.

The tool rides the connected-apps server, so it is there for every person-started turn, with or
without an app granted and with or without a team."""
import pytest
from sqlalchemy import delete, select

from app import composio_tools
from app.db.models import Event
from app.db.models.user_credential import UserCredential
from app.services import agent_store, app_connection, composio_policy
from app.services.provider_adapter import TurnOutcome
from tests.services.test_composio_bridge import PlainEngine, ask, fake, one_turn, world  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions, wait_until  # noqa: F401


def active(app, account_id):
    return {"id": account_id, "toolkit": {"slug": app}, "alias": None, "status": "ACTIVE", "user_id": "u1"}


async def no_grants(sessions, agent):
    async with sessions() as db:
        await agent_store.update_agent(db, agent.id, connector_scopes={}, connector_account_scopes={})


async def cards(sessions):
    async with sessions() as db:
        return [e.data for e in (await db.execute(select(Event).where(Event.type == app_connection.EVENT))).scalars()]


async def connect(world, args):
    seen = {}
    async def handler(ctx, sink, gate):
        seen["ctx"] = ctx
        seen["answer"] = await ask(ctx, gate, composio_tools.CONNECT, args)
        return TurnOutcome("succeeded", result="ok")
    await one_turn(world, handler)
    return seen


async def test_an_agent_with_no_apps_can_still_ask_for_one(world, fake, sessions):
    runtime, engine, agent = world
    await no_grants(sessions, agent)
    seen = await connect(world, {"app": "gmail", "reason": "Rank the support inbox"})
    assert seen["ctx"].composio_connect is True and seen["ctx"].composio_apps == {}
    assert "--connect" in seen["ctx"].composio_launch.args
    assert seen["answer"]["state"] == "needs_connection" and "End your turn" in seen["answer"]["next"]
    (card,) = await cards(sessions)
    assert card["app"] == "gmail" and card["agent_id"] == agent.id and card["reason"] == "Rank the support inbox"
    assert card["request_id"] == seen["answer"]["request_id"]
    assert fake.executes == []


async def test_without_a_key_it_says_needs_key(world, fake, sessions, owner):
    runtime, engine, agent = world
    await no_grants(sessions, agent)
    async with sessions() as db:
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()
    seen = await connect(world, {"app": "gmail"})
    assert seen["answer"]["state"] == "needs_key" and "Never ask for a key" in seen["answer"]["next"]


async def test_connected_but_not_allowed_then_ready(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(active("gmail", "ca_g"))
    seen = await connect(world, {"app": "gmail"})  # the agent only has github
    assert seen["answer"]["state"] == "needs_access" and "cannot grant yourself" in seen["answer"]["next"]
    async with sessions() as db:
        await agent_store.update_agent(db, agent.id, connector_scopes={"gmail": "read"}, connector_account_scopes={"gmail": ["ca_g"]})
    seen = await connect(world, {"app": "gmail"})
    assert seen["answer"]["state"] == "ready"


async def test_a_failed_account_is_not_connected(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append({**active("gmail", "ca_g"), "status": "FAILED"})
    assert (await connect(world, {"app": "gmail"}))["answer"]["state"] == "needs_connection"


@pytest.mark.parametrize("args", [{"app": "../etc"}, {"app": 5}, {"app": "gmail", "token": "x"}, {"app": "gmail", "reason": "x" * 201}])
async def test_bad_arguments_are_refused_and_record_nothing(world, fake, sessions, args):
    seen = await connect(world, args)
    assert seen["answer"]["code"] in {"invalid_input", "unsupported_app"} and await cards(sessions) == []


async def test_an_engine_without_connected_apps_gets_no_server(world, fake, sessions):
    runtime, engine, agent = world
    runtime.adapters["claude"] = plain = PlainEngine()
    seen = {}
    async def handler(ctx, sink, gate):
        seen["launch"], seen["connect"] = ctx.composio_launch, ctx.composio_connect
        return TurnOutcome("succeeded", result="ok")
    plain.handler = handler
    thread = await runtime.create_thread(agent_id=agent.id)
    turn = await runtime.send_turn(thread.id, "go")
    await wait_until(lambda: turn.id not in runtime._runs)
    assert seen == {"launch": None, "connect": False}


def test_the_tool_list_and_note():
    assert [t["name"] for t in composio_tools.mcp_tools({}, connect=True)] == [composio_tools.CONNECT]
    assert [t["name"] for t in composio_tools.mcp_tools({})] == ["list_accounts"]  # the old listing-only shape is unchanged
    assert composio_tools.CONNECT in [t["name"] for t in composio_tools.mcp_tools({"github": "read"}, connect=True)]
    assert composio_tools.CONNECT not in [t["name"] for t in composio_tools.mcp_tools({"github": "read"})]
    assert "request_app_connection" in composio_tools.tool_note({}, True) and composio_tools.tool_note({}) == ""
    assert composio_tools.tool_note({"github": "read"}, True).endswith(composio_tools.CONNECT_NOTE)


def test_policy_allows_the_request_even_with_no_apps_and_nothing_else():
    prefix = composio_policy.PREFIX
    assert composio_policy.automatic(prefix + composio_tools.CONNECT, "ask", {}) == ("allowed", "composio_connect_request")
    assert composio_policy.automatic(prefix + "github_get_authenticated_user", "ask", {}) == ("denied", "no_connected_apps")
