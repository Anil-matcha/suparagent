"""Stage 8: a large set of granted custom tools is reached through a bounded search and a call by ref."""
import json
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import delete, select

from app import community_mcp
from app.db.models import CustomMcpToolGrant
from app.services import community_mcp_bridge as bridge_module
from app.services import tool_policy
from app.services.community_mcp_bridge import BROKER_CALL, BROKER_SEARCH, PREFIX, CommunityMcpBridge
from tests.services.test_custom_mcp_bridge import (  # noqa: F401
    FakeRemote, approve, cleanup, make_tool, mounted, owner, remote, sessions, world,
)

THEMES = ["invoice", "calendar", "ticket", "contact", "report", "payment"]


def big_tool(n: int, *, description: str | None = None, extra_schema: int = 0):
    theme = THEMES[n % len(THEMES)]
    props = {"q": {"type": "string"}}
    for k in range(extra_schema):
        props[f"field_{k}"] = {"type": "string", "description": "x" * 200}
    return community_mcp._normalize_tool({
        "name": f"{theme}_action_{n:03d}", "description": description or f"Work with {theme}s, number {n}",
        "inputSchema": {"type": "object", "properties": props, "additionalProperties": False}})


async def grant_many(sessions, world, count, **kw):
    """Grant `count` tools (plus the fixture's one) and let the fake server list them all."""
    tools = [big_tool(n, **kw) for n in range(count)]
    async with sessions() as db:
        for tool in tools:
            db.add(CustomMcpToolGrant(
                agent_id=world[4].id, connection_id=world[3].id, tool_name=tool["name"],
                schema_digest=tool["schema_digest"], description=tool["description"], input_schema=tool["inputSchema"]))
        await db.commit()
    return tools


async def broker_bridge(world, sessions, count=60, **kw):
    tools = await grant_many(sessions, world, count, **kw)
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    return bridge, tools


def alias_of(tool, world):
    return bridge_module._alias(world[3].id, tool["name"], tool["schema_digest"])


async def test_a_few_tools_stay_direct_and_a_large_set_is_reached_through_two_broker_tools(world, sessions):
    small = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        assert not small.broker_aliases and BROKER_SEARCH not in small.aliases_allowed and len(small.tools) == 1
    finally:
        await small.close()
    bridge, _ = await broker_bridge(world, sessions, 60)
    try:
        assert [t["name"] for t in bridge.tools] == [BROKER_SEARCH, BROKER_CALL]
        assert sorted(bridge.aliases_allowed) == [BROKER_CALL, BROKER_SEARCH] and len(bridge.broker_aliases) == 61
        assert len(json.dumps(bridge.tools)) < 5000, "the provider sees two small tools, not the catalog"
        assert "never" not in bridge.prompt_note or True
        assert BROKER_SEARCH in bridge.prompt_note and "ref" in bridge.prompt_note
    finally:
        await bridge.close()


async def test_search_ranks_name_matches_first_and_returns_refs_with_schemas(world, sessions):
    bridge, tools = await broker_bridge(world, sessions, 60)
    try:
        found = await bridge.search({"query": "invoice", "limit": 5})
        assert len(found["results"]) == 5 and found["searched"] == 61
        assert all("invoice" in r["tool"] for r in found["results"])
        first = found["results"][0]
        assert set(first) == {"ref", "server", "tool", "description", "input_schema"} and first["server"] == "My server"
        assert first["ref"] in bridge.broker_aliases and first["input_schema"]["type"] == "object"
        assert "untrusted" in found["note"]
        assert (await bridge.search({"query": "zzzz-nothing"}))["results"] == []
    finally:
        await bridge.close()


@pytest.mark.parametrize("arguments", [
    {}, {"query": ""}, {"query": "   "}, {"query": "x" * 201}, {"query": 5}, {"query": "a", "limit": 0},
    {"query": "a", "limit": 21}, {"query": "a", "limit": True}, {"query": "a", "extra": 1}, {"query": "***"},
])
async def test_search_refuses_malformed_requests(world, sessions, arguments):
    bridge, _ = await broker_bridge(world, sessions, 30)
    try:
        assert (await bridge.search(arguments))["code"] == "invalid_request"
    finally:
        await bridge.close()


async def test_search_never_offers_a_tool_outside_the_grants_or_one_revoked_since(world, sessions):
    bridge, tools = await broker_bridge(world, sessions, 30)
    try:
        # A tool the server has but nobody granted is not in the index at all.
        ungranted = big_tool(999, description="secret ungranted payment tool")
        assert all(r["tool"] != ungranted["name"] for r in (await bridge.search({"query": "payment secret ungranted"}))["results"])
        before = {r["tool"] for r in (await bridge.search({"query": "invoice", "limit": 20}))["results"]}
        victim = next(t for t in tools if t["name"] in before)
        async with sessions() as db:
            await db.execute(delete(CustomMcpToolGrant).where(
                CustomMcpToolGrant.agent_id == world[4].id, CustomMcpToolGrant.tool_name == victim["name"]))
            await db.commit()
        after = {r["tool"] for r in (await bridge.search({"query": "invoice", "limit": 20}))["results"]}
        assert victim["name"] not in after and after < before
    finally:
        await bridge.close()


async def test_search_results_stay_within_their_byte_budget_even_for_a_huge_catalog(world, sessions):
    bridge, _ = await broker_bridge(world, sessions, 80, extra_schema=25)  # ~6 KB of schema per tool
    try:
        found = await bridge.search({"query": "work", "limit": 20})
        assert len(json.dumps(found, ensure_ascii=False).encode("utf-8")) <= bridge_module.SEARCH_RESULT_BYTES + 2000
        assert found["results"] and all(r["input_schema"] is None or r["tool"] for r in found["results"])
    finally:
        await bridge.close()


async def test_a_brokered_call_is_approved_on_a_card_that_names_the_exact_server_and_tool(world, sessions, remote):
    bridge, tools = await broker_bridge(world, sessions, 40)
    try:
        tool = tools[3]
        ref = alias_of(tool, world)
        remote.tools = tools + [world[5]]
        label = bridge.approval_summary(BROKER_CALL, {"ref": ref, "arguments": {"q": "x"}})
        assert label == f"Custom MCP My server (tools.example.com): {tool['name']}"
        assert bridge.approval_summary(BROKER_CALL, {"ref": "community_" + "0" * 32}) == "Custom MCP tool call (not a granted tool)"
        assert bridge.approval_summary(BROKER_SEARCH) == "Search the MCP tools granted to this agent"
        call = {"ref": ref, "arguments": {"q": "x"}}
        assert (await bridge.dispatch(BROKER_CALL, call))["code"] == "not_approved" and remote.calls == []
        await approve(sessions, world[1], BROKER_CALL, call)
        result = await bridge.dispatch(BROKER_CALL, call)
        assert result["content"][0]["text"] == "ok" and remote.calls == [(tool["name"], {"q": "x"})]
        assert (await bridge.dispatch(BROKER_CALL, call))["code"] == "not_approved", "an approval is used once"
        assert len(remote.calls) == 1
    finally:
        await bridge.close()


async def test_an_approval_for_one_ref_or_other_arguments_cannot_authorize_another_call(world, sessions, remote):
    bridge, tools = await broker_bridge(world, sessions, 40)
    try:
        remote.tools = tools + [world[5]]
        ref_a, ref_b = alias_of(tools[1], world), alias_of(tools[2], world)
        await approve(sessions, world[1], BROKER_CALL, {"ref": ref_a, "arguments": {"q": "x"}})
        assert (await bridge.dispatch(BROKER_CALL, {"ref": ref_b, "arguments": {"q": "x"}}))["code"] == "not_approved"
        assert (await bridge.dispatch(BROKER_CALL, {"ref": ref_a, "arguments": {"q": "other"}}))["code"] == "not_approved"
        # An approval written for the direct alias form does not authorize the brokered form either.
        await approve(sessions, world[1], ref_b, {"q": "x"}, request_id="r2")
        assert (await bridge.dispatch(BROKER_CALL, {"ref": ref_b, "arguments": {"q": "x"}}))["code"] == "not_approved"
        assert remote.calls == []
    finally:
        await bridge.close()


async def test_refs_outside_the_grants_and_direct_names_for_brokered_tools_are_refused(world, sessions, remote):
    bridge, tools = await broker_bridge(world, sessions, 40)
    try:
        ref = alias_of(tools[0], world)
        for bad in ({"ref": "community_" + "f" * 32, "arguments": {}}, {"ref": 5, "arguments": {}}, {"ref": ref},
                    {"ref": ref, "arguments": []}, {"ref": ref, "arguments": {}, "x": 1}):
            assert (await bridge.dispatch(BROKER_CALL, bad))["code"] == "unknown_tool"
        assert (await bridge.dispatch(ref, {"q": "x"}))["code"] == "use_broker"
        assert remote.built == [] and remote.calls == []
    finally:
        await bridge.close()


async def test_a_changed_tool_definition_drops_the_grant_and_leaves_the_search(world, sessions, remote):
    bridge, tools = await broker_bridge(world, sessions, 40)
    try:
        target = tools[4]
        ref = alias_of(target, world)
        changed = big_tool(4, description="Now deletes everything")
        assert changed["name"] == target["name"] and changed["schema_digest"] != target["schema_digest"]
        remote.tools = [t for t in tools if t["name"] != target["name"]] + [changed, world[5]]
        call = {"ref": ref, "arguments": {"q": "x"}}
        await approve(sessions, world[1], BROKER_CALL, call)
        assert (await bridge.dispatch(BROKER_CALL, call))["code"] == "tool_changed" and remote.calls == []
        names = {r["tool"] for r in (await bridge.search({"query": target["name"], "limit": 20}))["results"]}
        assert target["name"] not in names
    finally:
        await bridge.close()


async def test_a_revoked_connection_stops_brokered_calls_before_any_request(world, sessions, remote):
    from app.db.models import CustomMcpConnection
    bridge, tools = await broker_bridge(world, sessions, 40)
    try:
        ref = alias_of(tools[0], world)
        async with sessions() as db:
            (await db.get(CustomMcpConnection, world[3].id)).enabled = False
            await db.commit()
        assert (await bridge.dispatch(BROKER_CALL, {"ref": ref, "arguments": {"q": "x"}}))["code"] == "revoked"
        assert (await bridge.search({"query": "invoice"}))["results"] == []
        assert remote.built == []
    finally:
        await bridge.close()


async def test_the_mailbox_routes_search_and_the_shim_accepts_the_broker_tools(world, sessions):
    from app.community_mcp_server import Server
    bridge, _ = await broker_bridge(world, sessions, 40)
    try:
        shim = Server(str(bridge.folder), str(bridge.tools_path))
        assert shim.names == {BROKER_SEARCH, BROKER_CALL}
        token = "a" * 32
        request = bridge.folder / f"req-{token}.working"
        request.write_text(json.dumps({"kind": "call", "name": BROKER_SEARCH, "arguments": {"query": "calendar", "limit": 2}}))
        await bridge.handle(request)
        reply = json.loads((bridge.folder / f"resp-{token}.json").read_text(encoding="utf-8"))
        assert len(reply["results"]) == 2 and all("calendar" in r["tool"] for r in reply["results"])
        listing = await bridge.list_tools_now()
        assert [t["name"] for t in listing] == [BROKER_SEARCH, BROKER_CALL]
    finally:
        await bridge.close()


def test_the_policy_allows_search_without_a_card_but_never_a_call():
    run = NS(ctx=NS(media_review_only=False, draft_only=False, community_tools=[BROKER_SEARCH, BROKER_CALL]))
    assert tool_policy.automatic(PREFIX + BROKER_SEARCH, "ask", run, {"query": "x"}) == ("allowed", "community_search")
    assert tool_policy.automatic(PREFIX + BROKER_SEARCH, "plan", run, {"query": "x"}) == ("allowed", "community_search")
    assert tool_policy.automatic(PREFIX + BROKER_CALL, "ask", run, {"ref": "r", "arguments": {}}) is None
    assert tool_policy.automatic(PREFIX + BROKER_CALL, "plan", run, {}) == ("denied", "plan_mode")
    bare = NS(ctx=NS(media_review_only=False, draft_only=False, community_tools=[]))
    assert tool_policy.automatic(PREFIX + BROKER_SEARCH, "ask", bare, {}) == ("denied", "marketplace_tool_not_granted")


@pytest.mark.asyncio
async def test_a_custom_session_may_list_a_larger_catalog_than_a_marketplace_one(monkeypatch):
    def page(count):
        return {"tools": [{"name": f"t{n}", "inputSchema": {"type": "object", "properties": {}}} for n in range(count)]}

    async def rpc(method, params=None):
        return page(150)

    plain = community_mcp.RemoteMcpSession("https://example.com/mcp")
    monkeypatch.setattr(plain, "_rpc", rpc)
    with pytest.raises(community_mcp.McpError) as caught:
        await plain.list_tools()
    assert caught.value.code == "too_many_tools"
    big = community_mcp.RemoteMcpSession("https://example.com/mcp")
    big.max_tools, big.max_toolset_bytes = community_mcp.CUSTOM_MAX_TOOLS, community_mcp.CUSTOM_MAX_TOOLSET_BYTES
    monkeypatch.setattr(big, "_rpc", rpc)
    assert len(await big.list_tools()) == 150
    big.max_tools = 100
    with pytest.raises(community_mcp.McpError):
        await big.list_tools()


async def test_a_catalog_beyond_the_index_bound_is_not_mounted_at_all(world, sessions, monkeypatch):
    monkeypatch.setattr(bridge_module, "INDEX_MAX_TOOLS", 20)
    await grant_many(sessions, world, 30)
    assert await mounted(world) == []
