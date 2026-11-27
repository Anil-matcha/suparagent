"""AgentPhone: numbers are named by id or by +E.164, or only through an AgentPhone agent.

Written against the tool definitions AgentPhone reported on a live account (2026-11-17)."""
import json
from types import SimpleNamespace as NS

import pytest

from app import community_mcp
from app.db.models import AgentServiceProvider, AgentServiceResource, CustomMcpToolGrant
from app.services import agent_service_policy as policy
from app.services import tool_policy
from app.services.community_mcp_bridge import PREFIX, CommunityMcpBridge
from tests.services.test_agent_service_bridge import assign, provider  # noqa: F401
from tests.services.test_custom_mcp_bridge import FakeRemote, approve, mounted, remote, world  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401


def phone_tool(name, props, required=()):
    return {"name": name, "description": name, "inputSchema": {"type": "object", "required": list(required), "properties": {
        key: {"type": "string"} for key in props}}}


PHONE_TOOLS = [
    phone_tool("make_call", ["agent_id", "to_number", "from_number_id"], ["agent_id", "to_number"]),
    phone_tool("send_message", ["to_number", "number_id", "from_number", "body"]),
    phone_tool("list_numbers", ["limit"]),
    phone_tool("buy_number", ["country", "confirm"]),
]
NUM, NUM_ID = "+14155550100", "num_assigned"
OTHER_NUM, OTHER_ID = "+14155550199", "num_other"
LIST_NUMBERS_REPLY = {"data": [{"id": NUM_ID, "phoneNumber": NUM, "status": "active"},
                               {"id": OTHER_ID, "phoneNumber": OTHER_NUM, "status": "active"}], "hasMore": False, "total": 2}


@pytest.fixture
async def phone(world, provider, sessions, remote):
    """The provider is AgentPhone with one assigned number (address and id); buy_number is granted in the database too."""
    tools = [community_mcp._normalize_tool(t) for t in PHONE_TOOLS]
    async with sessions() as db:
        row = await db.get(AgentServiceProvider, world[3].id)
        row.provider = "agentphone"
        resource = await db.get(AgentServiceResource, provider)
        resource.kind, resource.address, resource.provider_resource_id = "phone", NUM, NUM_ID
        for tool in tools:
            db.add(CustomMcpToolGrant(
                agent_id=world[4].id, connection_id=world[3].id, tool_name=tool["name"],
                schema_digest=tool["schema_digest"], description=tool["description"], input_schema=tool["inputSchema"]))
        await db.commit()
    remote.tools = [world[5], *tools]
    await assign(sessions, world[4].id, provider)
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    by_tool = {item["remote_name"]: alias for alias, item in bridge.aliases.items()}
    yield bridge, by_tool
    await bridge.close()


def test_a_send_or_call_must_name_an_assigned_sender_number():
    def refused(tool, **args):
        return policy.refusal("agentphone", {}, args, [NUM], tool_name=tool, identifiers=[NUM_ID])

    # No sender named: the AgentPhone agent's own number would be used, so it is refused.
    assert refused("make_call", agent_id="a1", to_number="+1999") == "resource_not_assigned"
    assert refused("make_call", agent_id="a1", to_number="+1999", from_number_id=OTHER_ID) == "resource_not_assigned"
    assert refused("make_call", agent_id="a1", to_number="+1999", from_number_id=NUM_ID) is None
    assert refused("make_conversation_call", agent_id="a1", to_number="+1", topic="t", from_number_id=NUM_ID) is None
    assert refused("send_message", to_number="+1999", body="hi") == "resource_not_assigned"
    assert refused("send_message", to_number="+1999", number_id=NUM_ID) is None
    assert refused("send_message", to_number="+1999", from_number="+1 415 555 0100") is None
    assert refused("send_message", to_number="+1999", from_number=OTHER_NUM) == "resource_not_assigned"
    # Both named: both must be assigned.
    assert refused("send_message", to_number="+1", number_id=NUM_ID, from_number=OTHER_NUM) == "resource_not_assigned"
    assert refused("send_message", to_number="+1", number_id=7) == "resource_not_assigned"
    assert refused("send_message", to_number="+1", number_id=None, from_number=None) == "resource_not_assigned"


def test_number_scoped_reads_must_name_an_assigned_number_and_account_wide_tools_are_not_narrowed():
    def check(tool, **args):
        return policy.refusal("agentphone", {}, args, [NUM], tool_name=tool, identifiers=[NUM_ID])

    for tool in ("check_readiness", "get_messages", "list_calls"):
        assert check(tool) == "resource_not_assigned", tool
        assert check(tool, number_id=OTHER_ID) == "resource_not_assigned", tool
        assert check(tool, number_id=NUM_ID) is None, tool
    assert check("watch_messages", number_ids=[NUM_ID]) is None
    assert check("watch_messages", number_ids=[NUM_ID, OTHER_ID]) == "resource_not_assigned"
    assert check("watch_messages", number_ids=[]) == "resource_not_assigned"
    assert check("watch_messages", number_ids=NUM_ID) == "resource_not_assigned"
    for tool in ("account_overview", "get_usage", "list_agents", "get_call", "list_contacts", "list_numbers"):
        assert check(tool) is None, tool


def test_a_number_recorded_without_an_id_never_matches_an_id_argument():
    args = {"from_number_id": NUM_ID, "to_number": "+1", "agent_id": "a"}
    assert policy.refusal("agentphone", {}, args, [NUM], tool_name="make_call", identifiers=[]) == "resource_not_assigned"


def test_tools_that_spend_money_or_set_up_inbound_are_blocked_for_phone_only():
    for tool in ("buy_number", "set_webhook", "delete_webhook", "delete_agent"):
        assert policy.blocked("agentphone", tool)
        assert policy.refusal("agentphone", {}, {}, [NUM], tool_name=tool) == "tool_not_allowed"
        assert not policy.blocked("agentmail", tool) and not policy.blocked("other", tool)
    assert not policy.blocked("agentphone", "make_call")


async def test_a_blocked_tool_is_not_mounted_even_though_it_is_granted(phone):
    bridge, tools = phone
    assert "buy_number" not in tools and {"make_call", "send_message", "list_numbers"} <= set(tools)


async def test_a_call_from_another_number_is_refused_before_any_card_or_request(phone, remote, sessions, world):
    bridge, tools = phone
    alias = tools["make_call"]
    args = {"agent_id": "a1", "to_number": "+14155550123", "from_number_id": OTHER_ID}
    run = NS(ctx=NS(media_review_only=False, draft_only=False, community_tools=[alias], community_bridge=bridge))
    assert tool_policy.automatic(PREFIX + alias, "ask", run, args) == ("denied", "resource_not_assigned")
    await approve(sessions, world[1], alias, args)
    assert (await bridge.execute(alias, args))["code"] == "resource_not_assigned"
    assert remote.calls == []


async def test_a_call_from_the_assigned_number_needs_its_own_approval_and_the_card_names_it(phone, remote, sessions, world):
    bridge, tools = phone
    alias = tools["make_call"]
    args = {"agent_id": "a1", "to_number": "+14155550123", "from_number_id": NUM_ID}
    assert (await bridge.execute(alias, args))["code"] == "not_approved"
    assert "agent number: " + NUM in bridge.approval_summary(alias, args)
    await approve(sessions, world[1], alias, args)
    assert (await bridge.execute(alias, args))["content"][0]["text"] == "ok"
    assert remote.calls == [("make_call", args)]


async def test_list_numbers_shows_only_the_assigned_numbers(phone, remote, sessions, world):
    bridge, tools = phone
    alias = tools["list_numbers"]
    remote.reply = {"content": [{"type": "text", "text": json.dumps(LIST_NUMBERS_REPLY)}],
                    "structuredContent": LIST_NUMBERS_REPLY, "isError": False}
    await approve(sessions, world[1], alias, {})
    result = await bridge.execute(alias, {})
    kept = result["structuredContent"]
    assert [row["id"] for row in kept["data"]] == [NUM_ID] and kept["total"] == 1
    assert OTHER_NUM not in json.dumps(result) and OTHER_ID not in json.dumps(result)


async def test_list_numbers_is_withheld_when_its_result_cannot_be_read(phone, remote, sessions, world):
    bridge, tools = phone
    alias = tools["list_numbers"]
    remote.reply = {"content": [{"type": "text", "text": "plain words"}], "isError": False}
    await approve(sessions, world[1], alias, {})
    result = await bridge.execute(alias, {})
    assert result["code"] == "result_not_filterable" and "plain words" not in json.dumps(result)
