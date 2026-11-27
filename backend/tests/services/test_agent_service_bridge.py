"""A mailbox/number provider's tools reach an agent only while one of its resources is assigned to it."""
import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app import community_mcp
from app.db.models import (
    Agent, AgentServiceAssignment, AgentServiceAudit, AgentServiceProvider, AgentServiceResource,
)
from app.services import approval_modes, thread_store, tool_policy
from app.services.agent_service_store import remove_provider_data
from app.services.community_mcp_bridge import PREFIX, CommunityMcpBridge
from tests.services.test_custom_mcp_bridge import FakeRemote, approve, mounted, remote, world  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401


@pytest.fixture
async def provider(world, sessions, owner):
    """The world's connection, marked as a provider holding one resource that no agent has yet."""
    connection = world[3]
    resource_id = uuid4().hex
    async with sessions() as db:
        db.add(AgentServiceProvider(connection_id=connection.id, provider="agentmail", resource_limit=3))
        await db.flush()
        db.add(AgentServiceResource(id=resource_id, owner_id=owner, connection_id=connection.id, kind="email",
                                    address="helper@agentmail.to", label="Helper inbox"))
        await db.commit()
    yield resource_id
    async with sessions() as db:
        await remove_provider_data(db, connection.id)
        await db.commit()


async def assign(sessions, agent_id, resource_id):
    async with sessions() as db:
        db.add(AgentServiceAssignment(agent_id=agent_id, resource_id=resource_id))
        await db.commit()


async def test_a_provider_with_nothing_assigned_to_the_agent_is_not_mounted(world, provider):
    assert await mounted(world) == []


async def test_a_resource_assigned_to_another_agent_does_not_mount_it_here(world, provider, sessions, owner):
    async with sessions() as db:
        other = Agent(owner_id=owner, name="Someone else")
        db.add(other)
        await db.commit()
    try:
        await assign(sessions, other.id, provider)
        assert await mounted(world) == []
    finally:
        async with sessions() as db:
            await db.execute(delete(AgentServiceAssignment).where(AgentServiceAssignment.agent_id == other.id))
            await db.delete(await db.get(Agent, other.id))
            await db.commit()


async def test_an_assigned_resource_mounts_the_providers_granted_tools(world, provider, sessions):
    await assign(sessions, world[4].id, provider)
    [item] = await mounted(world)
    assert item["kind"] == "custom" and item["grant"]["tool_name"] == "lookup"


async def test_an_ordinary_connection_is_unaffected(world):
    assert len(await mounted(world)) == 1


async def test_removing_the_assignment_stops_a_running_turn_before_its_next_call(world, provider, sessions, remote):
    await assign(sessions, world[4].id, provider)
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        async with sessions() as db:
            await db.execute(delete(AgentServiceAssignment).where(AgentServiceAssignment.agent_id == world[4].id))
            await db.commit()
        result = await bridge.execute(alias, {"q": "x"})
        assert result["code"] == "revoked"
        assert remote.built == [] and remote.calls == []
    finally:
        await bridge.close()


async def test_the_approval_card_names_the_agent_owned_address_that_would_act(world, provider, sessions):
    await assign(sessions, world[4].id, provider)
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        summary = bridge.approval_summary(alias, {"q": "x"})
        assert summary == "Custom MCP My server (tools.example.com): lookup | agent address: helper@agentmail.to"
    finally:
        await bridge.close()


async def test_an_ordinary_connections_card_does_not_mention_an_agent_address(world):
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        assert "agent address" not in bridge.approval_summary(alias, {"q": "x"})
    finally:
        await bridge.close()


@pytest.mark.parametrize("mode", approval_modes.MODES)
async def test_no_approval_mode_lets_a_provider_tool_run_without_a_card(world, provider, sessions, mode):
    await assign(sessions, world[4].id, provider)
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        run = NS(ctx=NS(media_review_only=False, draft_only=False, community_tools=[alias]))
        decision = tool_policy.automatic(PREFIX + alias, mode, run, {"q": "x"})
        assert decision is None or decision[0] == "denied"
    finally:
        await bridge.close()


async def test_an_approval_for_one_recipient_does_not_cover_another(world, provider, sessions, remote):
    await assign(sessions, world[4].id, provider)
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        await approve(sessions, world[1], alias, {"q": "alice@example.com"})
        assert (await bridge.execute(alias, {"q": "bob@example.com"}))["code"] == "not_approved"
        assert remote.calls == []
    finally:
        await bridge.close()


async def test_a_denied_approval_never_reaches_the_provider(world, provider, sessions, remote):
    await assign(sessions, world[4].id, provider)
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        async with sessions() as db:
            row = await thread_store.create_approval(
                db, thread_id=world[1].thread_id, turn_id=world[1].turn_id, request_id="d1", tool=PREFIX + alias,
                input={"q": "x"}, summary="x", kind="other", owner_token=world[1].owner_token)
            await thread_store.decide_approval(db, row.id, "denied", by={"by": "person"})
        assert (await bridge.execute(alias, {"q": "x"}))["code"] == "not_approved"
        assert remote.calls == []
    finally:
        await bridge.close()


@pytest.fixture
async def audit_rows(sessions, owner):
    async def rows():
        async with sessions() as db:
            return (await db.scalars(select(AgentServiceAudit).where(AgentServiceAudit.owner_id == owner))).all()
    yield rows
    async with sessions() as db:
        await db.execute(delete(AgentServiceAudit).where(AgentServiceAudit.owner_id == owner))
        await db.commit()


async def test_an_approved_provider_call_is_recorded_without_its_arguments(world, provider, sessions, remote, audit_rows):
    await assign(sessions, world[4].id, provider)
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        await approve(sessions, world[1], alias, {"q": "the private message body"})
        assert (await bridge.execute(alias, {"q": "the private message body"}))["content"][0]["text"] == "ok"
        [row] = await audit_rows()
        assert (row.agent_id, row.agent_name, row.provider, row.tool_name) == (world[4].id, "Tools", "agentmail", "lookup")
        assert row.resources == ["helper@agentmail.to"] and row.outcome == "completed"
        assert row.connection_id == world[3].id and row.thread_id == world[1].thread_id
        stored = " ".join(str(getattr(row, column.name)) for column in AgentServiceAudit.__table__.columns)
        assert "private message body" not in stored and "sk_custom_SECRET_value" not in stored
    finally:
        await bridge.close()


async def test_a_call_that_was_not_approved_leaves_no_record(world, provider, sessions, remote, audit_rows):
    await assign(sessions, world[4].id, provider)
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        assert (await bridge.execute(alias, {"q": "x"}))["code"] == "not_approved"
        assert await audit_rows() == []
    finally:
        await bridge.close()


async def test_an_uncertain_outcome_is_recorded_as_such(world, provider, sessions, monkeypatch, audit_rows):
    class Flaky(FakeRemote):
        async def call_tool(self, name, arguments):
            raise community_mcp.McpError("remote_unavailable", "lost", 502)

    monkeypatch.setattr(community_mcp, "RemoteMcpSession", Flaky)
    await assign(sessions, world[4].id, provider)
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        await approve(sessions, world[1], alias, {"q": "x"})
        assert (await bridge.execute(alias, {"q": "x"}))["code"] == "outcome_unknown"
        [row] = await audit_rows()
        assert row.outcome == "outcome_unknown"
    finally:
        await bridge.close()


async def test_an_ordinary_connection_call_is_not_recorded(world, sessions, remote, audit_rows):
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        [alias] = bridge.aliases_allowed
        await approve(sessions, world[1], alias, {"q": "x"})
        assert (await bridge.execute(alias, {"q": "x"}))["content"][0]["text"] == "ok"
        assert await audit_rows() == []
    finally:
        await bridge.close()


# ---- argument-level rules, written against the tool definitions AgentMail reported on a live account ----

SEND_DRAFT = {"name": "send_draft", "description": "Send a draft", "inputSchema": {
    "type": "object", "properties": {"inboxId": {"type": "string"}, "draftId": {"type": "string"}},
    "required": ["inboxId", "draftId"], "additionalProperties": False}}
LIST_INBOXES = {"name": "list_inboxes", "description": "List inboxes", "inputSchema": {
    "type": "object", "properties": {"limit": {"type": "integer"}, "pageToken": {"type": "string"}},
    "additionalProperties": False}}
MINE, OTHER = "helper@agentmail.to", "someone-else@agentmail.to"
LIST_REPLY = {"inboxes": [
    {"inboxId": MINE, "email": MINE, "displayName": "Mine"},
    {"inboxId": OTHER, "email": OTHER, "displayName": "Other"}], "count": 2, "limit": 20}


@pytest.fixture
async def mail(world, provider, sessions, remote):
    """The provider also has AgentMail's send_draft and list_inboxes granted; the helper inbox is assigned."""
    tools = [community_mcp._normalize_tool(SEND_DRAFT), community_mcp._normalize_tool(LIST_INBOXES)]
    from app.db.models import CustomMcpToolGrant
    async with sessions() as db:
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


def test_the_rules_only_apply_to_a_provider_that_names_the_inbox_in_inboxid():
    from app.services import agent_service_policy as policy
    schema = community_mcp._normalize_tool(SEND_DRAFT)["inputSchema"]
    assert policy.refusal("agentmail", schema, {"inboxId": OTHER, "draftId": "d"}, [MINE]) == "resource_not_assigned"
    assert policy.refusal("agentmail", schema, {"inboxId": MINE.upper(), "draftId": "d"}, [MINE]) is None
    assert policy.refusal("agentmail", schema, {"draftId": "d"}, [MINE]) == "resource_not_assigned"
    assert policy.refusal("agentmail", schema, {"inboxId": 5}, [MINE]) == "resource_not_assigned"
    assert policy.refusal("other", schema, {"inboxId": OTHER}, [MINE]) is None
    assert policy.refusal("agentphone", schema, {"inboxId": OTHER}, [MINE]) is None
    no_inbox = community_mcp._normalize_tool(LIST_INBOXES)["inputSchema"]
    assert policy.refusal("agentmail", no_inbox, {}, [MINE]) is None


async def test_a_call_naming_another_inbox_is_refused_before_any_approval_or_request(mail, remote, sessions, world):
    bridge, tools = mail
    alias = tools["send_draft"]
    result = await bridge.execute(alias, {"inboxId": OTHER, "draftId": "d1"})
    assert result["code"] == "resource_not_assigned" and remote.calls == [] and remote.built == []
    # Even with a matching approval card on file the call is not made.
    await approve(sessions, world[1], alias, {"inboxId": OTHER, "draftId": "d1"})
    assert (await bridge.execute(alias, {"inboxId": OTHER, "draftId": "d1"}))["code"] == "resource_not_assigned"
    assert remote.calls == []


async def test_a_call_naming_the_assigned_inbox_goes_through_with_its_own_approval(mail, remote, sessions, world):
    bridge, tools = mail
    alias = tools["send_draft"]
    assert (await bridge.execute(alias, {"inboxId": MINE, "draftId": "d1"}))["code"] == "not_approved"
    await approve(sessions, world[1], alias, {"inboxId": MINE, "draftId": "d1"})
    result = await bridge.execute(alias, {"inboxId": MINE, "draftId": "d1"})
    assert result["content"][0]["text"] == "ok" and remote.calls == [("send_draft", {"inboxId": MINE, "draftId": "d1"})]


async def test_no_approval_card_is_offered_for_another_inbox(mail):
    bridge, tools = mail
    alias = tools["send_draft"]
    run = NS(ctx=NS(media_review_only=False, draft_only=False, community_tools=[alias], community_bridge=bridge))
    assert tool_policy.automatic(PREFIX + alias, "ask", run, {"inboxId": OTHER, "draftId": "d"}) == (
        "denied", "resource_not_assigned")
    assert tool_policy.automatic(PREFIX + alias, "ask", run, {"inboxId": MINE, "draftId": "d"}) is None


async def test_unassigning_the_inbox_after_the_turn_started_refuses_the_next_call(mail, sessions, world, provider, remote):
    bridge, tools = mail
    alias = tools["send_draft"]
    other = uuid4().hex
    async with sessions() as db:
        await db.execute(delete(AgentServiceAssignment).where(AgentServiceAssignment.agent_id == world[4].id))
        db.add(AgentServiceResource(id=other, owner_id=world[3].owner_id, connection_id=world[3].id, kind="email",
                                    address=OTHER, label="Other"))
        await db.flush()
        db.add(AgentServiceAssignment(agent_id=world[4].id, resource_id=other))
        await db.commit()
    await approve(sessions, world[1], alias, {"inboxId": MINE, "draftId": "d1"})
    assert (await bridge.execute(alias, {"inboxId": MINE, "draftId": "d1"}))["code"] == "resource_not_assigned"
    assert remote.calls == []


async def test_list_inboxes_returns_only_the_assigned_inboxes(mail, remote, sessions, world):
    bridge, tools = mail
    alias = tools["list_inboxes"]
    remote.reply = {"content": [{"type": "text", "text": json.dumps(LIST_REPLY)}], "structuredContent": LIST_REPLY,
                    "isError": False}
    await approve(sessions, world[1], alias, {})
    result = await bridge.execute(alias, {})
    kept = result["structuredContent"]
    assert [i["inboxId"] for i in kept["inboxes"]] == [MINE] and kept["count"] == 1
    assert OTHER not in json.dumps(result)


async def test_list_inboxes_is_withheld_when_its_result_cannot_be_read(mail, remote, sessions, world):
    bridge, tools = mail
    alias = tools["list_inboxes"]
    remote.reply = {"content": [{"type": "text", "text": "not json at all"}], "isError": False}
    await approve(sessions, world[1], alias, {})
    result = await bridge.execute(alias, {})
    assert result["code"] == "result_not_filterable" and "not json" not in json.dumps(result)


async def test_a_connection_that_is_not_agentmail_is_not_narrowed_by_inboxid(mail, sessions, world, remote):
    bridge, tools = mail
    async with sessions() as db:
        row = await db.get(AgentServiceProvider, world[3].id)
        row.provider = "other"
        await db.commit()
    bridge = CommunityMcpBridge(world[0], world[1], await mounted(world))
    try:
        alias = {i["remote_name"]: a for a, i in bridge.aliases.items()}["send_draft"]
        await approve(sessions, world[1], alias, {"inboxId": OTHER, "draftId": "d1"})
        result = await bridge.execute(alias, {"inboxId": OTHER, "draftId": "d1"})
        assert result["content"][0]["text"] == "ok"
    finally:
        await bridge.close()
