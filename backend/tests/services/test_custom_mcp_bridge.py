"""Owner-added MCP servers through the turn bridge: mounting, revocation, approval and secrets."""
import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app import community_mcp
from app.core import crypto
from app.core.config import settings
from app.db.models import Agent, CustomMcpConnection, CustomMcpToolGrant, Thread, Turn, UserCredential
from app.services import thread_store
from app.services.community_mcp_bridge import CUSTOM_CREDENTIAL_PREFIX, PREFIX, CommunityMcpBridge
from tests.services.thread_harness import sessions, owner, cleanup  # noqa: F401

SECRET = "sk_custom_SECRET_value"
ENDPOINT = "https://tools.example.com/mcp"


def make_tool(name="lookup", description="Look up"):
    return community_mcp._normalize_tool({"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": {"q": {"type": "string"}}, "additionalProperties": False}})


@pytest.fixture
async def world(sessions, owner, monkeypatch):
    monkeypatch.setattr(settings, "HOST_EXECUTION_DISABLED", False)
    tool = make_tool()
    async with sessions() as db:
        agent = Agent(owner_id=owner, name="Tools")
        db.add(agent)
        await db.flush()
        thread = Thread(owner_id=owner, agent_id=agent.id, provider="codex")
        db.add(thread)
        await db.flush()
        turn = Turn(thread_id=thread.id, seq=1, prompt_text="Go", origin="person", status="running", owner_token="owner-token")
        connection = CustomMcpConnection(
            id=uuid4().hex, owner_id=owner, name="My server", endpoint=ENDPOINT, auth_mode="header",
            auth_header_name="X-Api-Key", enabled=True, revision=3, tested_revision=3, tools_snapshot=[tool])
        db.add_all([turn, connection])
        await db.flush()
        db.add(CustomMcpToolGrant(
            agent_id=agent.id, connection_id=connection.id, tool_name="lookup", schema_digest=tool["schema_digest"],
            description=tool["description"], input_schema=tool["inputSchema"]))
        db.add(UserCredential(user_id=owner, provider=CUSTOM_CREDENTIAL_PREFIX + connection.id, file_path="-",
                              ciphertext=crypto.encrypt(json.dumps(SECRET))))
        await db.commit()
    runtime = NS(owner_id=owner, session=sessions)
    run = NS(agent_id=agent.id, routine_run_id=None, thread_id=thread.id, turn_id=turn.id, owner_token="owner-token",
             uninterrupted_access=False, ctx=NS(computer=None, browser=None, team=None, draft_only=False,
                                                media_review_only=False, aborted=False, community_tools=[], approval_mode="ask"))
    adapter = NS(key="claude", capabilities=NS(mcp=True, approvals=True))
    yield runtime, run, adapter, connection, agent, tool
    async with sessions() as db:
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.execute(delete(CustomMcpToolGrant).where(CustomMcpToolGrant.agent_id == agent.id))
        await db.execute(delete(CustomMcpConnection).where(CustomMcpConnection.id == connection.id))
        await db.delete(await db.get(Agent, agent.id))
        await db.commit()


class FakeRemote:
    """Stands in for the network session; records how it was built and called."""
    built: list = []
    tools: list = []
    calls: list = []
    reply: dict = {"content": [{"type": "text", "text": "ok"}]}

    def __init__(self, endpoint, token=None, *, header=None):
        FakeRemote.built.append((endpoint, token, header))

    async def list_tools(self):
        return list(FakeRemote.tools)

    async def call_tool(self, name, arguments):
        FakeRemote.calls.append((name, arguments))
        return FakeRemote.reply

    async def close(self):
        pass


@pytest.fixture
def remote(monkeypatch, world):
    FakeRemote.built, FakeRemote.calls = [], []
    FakeRemote.tools = [world[5]]
    FakeRemote.reply = {"content": [{"type": "text", "text": "ok"}]}
    monkeypatch.setattr(community_mcp, "RemoteMcpSession", FakeRemote)
    return FakeRemote


async def approve(sessions, run, alias, arguments, request_id="r1"):
    async with sessions() as db:
        row = await thread_store.create_approval(
            db, thread_id=run.thread_id, turn_id=run.turn_id, request_id=request_id, tool=PREFIX + alias,
            input=arguments, summary="x", kind="other", owner_token=run.owner_token)
        await thread_store.decide_approval(db, row.id, "allowed", by={"by": "person"})


async def mounted(world):
    runtime, run, adapter, *_ = world
    return await CommunityMcpBridge.for_turn(runtime, run, adapter)


async def test_an_enabled_tested_connection_with_a_grant_is_mounted_for_a_person_turn(world):
    [item] = await mounted(world)
    assert item["kind"] == "custom" and item["grant"]["tool_name"] == "lookup"
    bridge = CommunityMcpBridge(world[0], world[1], [item])
    try:
        [tool] = bridge.tools
        assert tool["description"].startswith("Custom MCP server: My server.")
        assert "tools.example.com" not in json.dumps(bridge.tools) and SECRET not in json.dumps(bridge.tools)
        [alias] = bridge.aliases_allowed
        assert bridge.approval_summary(alias) == "Custom MCP My server: lookup"
    finally:
        await bridge.close()


@pytest.mark.parametrize("change", [
    {"enabled": False},
    {"tested_revision": None},
    {"tested_revision": 2},
    {"auth_mode": "oauth"},
])
async def test_a_connection_that_is_not_enabled_and_current_is_not_mounted(world, sessions, change):
    async with sessions() as db:
        row = await db.get(CustomMcpConnection, world[3].id)
        for key, value in change.items():
            setattr(row, key, value)
        await db.commit()
    assert await mounted(world) == []


async def test_a_missing_secret_leaves_the_tool_unmounted(world, sessions, owner):
    async with sessions() as db:
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()
    assert await mounted(world) == []


async def test_custom_tools_are_never_mounted_for_routines_rooms_or_computers(world):
    runtime, run, adapter, *_ = world
    run.routine_run_id = 5
    assert await CommunityMcpBridge.for_turn(runtime, run, adapter) == []
    run.routine_run_id = None
    run.ctx.computer = NS(computer_id=1)
    assert await CommunityMcpBridge.for_turn(runtime, run, adapter) == []
    run.ctx.computer = None
    adapter.key = "gemini"
    assert await CommunityMcpBridge.for_turn(runtime, run, adapter) == []


async def test_a_routine_style_run_cannot_use_a_custom_tool_even_if_one_was_mounted(world):
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        assert await bridge._current_snapshot(alias) is not None
        world[1].uninterrupted_access = True
        assert await bridge._current_snapshot(alias) is None
    finally:
        await bridge.close()


@pytest.mark.parametrize("revoke", ["disable", "revision", "grant", "delete", "untested"])
async def test_revocation_blocks_the_next_call_before_any_request(world, sessions, remote, revoke):
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        async with sessions() as db:
            row = await db.get(CustomMcpConnection, world[3].id)
            if revoke == "disable":
                row.enabled = False
            elif revoke == "revision":
                row.revision += 1
                row.tested_revision = row.revision
            elif revoke == "untested":
                row.tested_revision = None
            elif revoke == "grant":
                await db.execute(delete(CustomMcpToolGrant).where(CustomMcpToolGrant.agent_id == world[4].id))
            else:
                await db.execute(delete(CustomMcpToolGrant).where(CustomMcpToolGrant.agent_id == world[4].id))
                await db.delete(row)
            await db.commit()
        result = await bridge.execute(alias, {"q": "x"})
        assert result["code"] == "revoked"
        assert remote.built == [] and remote.calls == []
    finally:
        await bridge.close()


async def test_a_call_needs_an_approval_for_exactly_these_arguments(world, sessions, remote):
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        assert (await bridge.execute(alias, {"q": "x"}))["code"] == "not_approved"
        await approve(sessions, world[1], alias, {"q": "other"})
        assert (await bridge.execute(alias, {"q": "x"}))["code"] == "not_approved"
        assert remote.calls == []
        await approve(sessions, world[1], alias, {"q": "x"}, request_id="r2")
        result = await bridge.execute(alias, {"q": "x"})
        assert result["content"][0]["text"] == "ok" and remote.calls == [("lookup", {"q": "x"})]
        # An approval is consumed once: the same call cannot be replayed.
        assert (await bridge.execute(alias, {"q": "x"}))["code"] == "not_approved"
        assert len(remote.calls) == 1
    finally:
        await bridge.close()


async def test_the_secret_goes_to_the_named_header_only_inside_the_backend(world, sessions, remote):
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        await approve(sessions, world[1], alias, {"q": "x"})
        remote.reply = {"content": [{"type": "text", "text": f"echo {SECRET}"}]}
        result = await bridge.execute(alias, {"q": "x"})
        assert remote.built == [(ENDPOINT, SECRET, "X-Api-Key")]
        assert SECRET not in json.dumps(result) and "[credential redacted]" in json.dumps(result)
        assert SECRET not in bridge.tools_path.read_text(encoding="utf-8")
        assert SECRET not in json.dumps(bridge.launch.args)
    finally:
        await bridge.close()


async def test_bearer_connections_send_a_bearer_secret_and_no_header_name(world, sessions, remote):
    async with sessions() as db:
        row = await db.get(CustomMcpConnection, world[3].id)
        row.auth_mode, row.auth_header_name = "bearer", None
        await db.commit()
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        await approve(sessions, world[1], alias, {})
        await bridge.execute(alias, {})
        assert remote.built == [(ENDPOINT, SECRET, None)]
    finally:
        await bridge.close()


async def test_a_changed_tool_definition_removes_the_grant_and_is_never_called(world, sessions, remote):
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        await approve(sessions, world[1], alias, {"q": "x"})
        remote.tools = [make_tool(description="Now also deletes things")]
        result = await bridge.execute(alias, {"q": "x"})
        assert result["code"] == "tool_changed" and remote.calls == []
        async with sessions() as db:
            assert (await db.scalars(select(CustomMcpToolGrant).where(
                CustomMcpToolGrant.agent_id == world[4].id))).first() is None
    finally:
        await bridge.close()


async def test_arguments_outside_the_granted_schema_never_reach_the_server(world, sessions, remote):
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        await approve(sessions, world[1], alias, {"q": 1})
        result = await bridge.execute(alias, {"q": 1})
        assert result["code"] == "bad_arguments" and remote.built == [] and remote.calls == []
    finally:
        await bridge.close()


async def test_marketplace_and_custom_tools_for_one_agent_do_not_collide(world, sessions):
    from app.db.models import CommunityMcpConnection, CommunityMcpToolGrant
    runtime, run, adapter, connection, agent, tool = world
    async with sessions() as db:
        listing = CommunityMcpConnection(
            id=uuid4().hex, owner_id=runtime.owner_id, slug="pub-" + uuid4().hex[:8], section="community",
            manifest={"auth_mode": "none", "name": "Public", "slug": "public", "endpoint": "https://example.com/mcp"},
            manifest_digest="m1", revision=1)
        db.add(listing)
        await db.flush()
        db.add(CommunityMcpToolGrant(agent_id=agent.id, connection_id=listing.id, tool_name="lookup",
                                     schema_digest=tool["schema_digest"], description="Look up",
                                     input_schema=tool["inputSchema"]))
        await db.commit()
    try:
        items = await CommunityMcpBridge.for_turn(runtime, run, adapter)
        assert sorted(item.get("kind", "marketplace") for item in items) == ["custom", "marketplace"]
        bridge = CommunityMcpBridge(runtime, run, items)
        try:
            assert len(bridge.aliases_allowed) == 2
        finally:
            await bridge.close()
    finally:
        async with sessions() as db:
            await db.execute(delete(CommunityMcpToolGrant).where(CommunityMcpToolGrant.agent_id == agent.id))
            await db.execute(delete(CommunityMcpConnection).where(CommunityMcpConnection.id == listing.id))
            await db.commit()


async def _make_oauth(sessions, world, owner, *, expires_in=-10):
    import time
    from app import custom_mcp_oauth as oauth
    async with sessions() as db:
        row = await db.get(CustomMcpConnection, world[3].id)
        row.auth_mode, row.auth_header_name = "oauth", None
        row.oauth_config = {"server": {"token_endpoint": "https://auth.example.com/token", "resource": ENDPOINT},
                            "effective_client_id": "client-1"}
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await oauth.save_record(db, owner, row.id, {
            "access_token": "at-old", "refresh_token": "rt-old", "expires_at": int(time.time()) + expires_in}, None)
        await db.commit()


async def test_an_oauth_connection_is_mounted_only_while_signed_in(world, sessions, owner):
    from app import custom_mcp_oauth as oauth
    await _make_oauth(sessions, world, owner)
    assert len(await mounted(world)) == 1
    async with sessions() as db:
        await oauth.delete_record(db, owner, world[3].id)
        await db.commit()
    assert await mounted(world) == []


async def test_an_oauth_call_uses_a_refreshed_token_that_never_reaches_the_provider(world, sessions, remote, owner, monkeypatch):
    import time
    from app import custom_mcp_oauth as oauth
    await _make_oauth(sessions, world, owner)
    refreshes = []

    async def fake_refresh(config, *, client_id, client_secret, record):
        refreshes.append(record["refresh_token"])
        return {"access_token": "at-new", "refresh_token": "rt-new", "expires_at": int(time.time()) + 3600}

    monkeypatch.setattr(oauth, "refresh_tokens", fake_refresh)
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        await approve(sessions, world[1], alias, {"q": "x"})
        result = await bridge.execute(alias, {"q": "x"})
        assert result["content"][0]["text"] == "ok"
        assert remote.built == [(ENDPOINT, "at-new", None)] and refreshes == ["rt-old"]
        assert "at-new" not in bridge.tools_path.read_text(encoding="utf-8") and "at-new" not in json.dumps(bridge.launch.args)
    finally:
        await bridge.close()


async def test_a_refresh_the_server_rejects_stops_the_call_and_signs_the_connection_out(world, sessions, remote, owner, monkeypatch):
    from app import custom_mcp_oauth as oauth
    await _make_oauth(sessions, world, owner)

    async def dead(config, *, client_id, client_secret, record):
        raise oauth.OAuthError("invalid_grant", "dead", 400)

    monkeypatch.setattr(oauth, "refresh_tokens", dead)
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        await approve(sessions, world[1], alias, {"q": "x"})
        result = await bridge.execute(alias, {"q": "x"})
        assert result["code"] == "needs_sign_in" and remote.calls == []
        async with sessions() as db:
            row = await db.get(CustomMcpConnection, world[3].id)
            assert row.enabled is False and not await oauth.is_signed_in(db, owner, row.id)
    finally:
        await bridge.close()
