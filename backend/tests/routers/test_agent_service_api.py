"""/api/v1/integrations/agent-services: provider designation, resources and agent assignment.

Everything starts empty: a provider has no resources, a resource has no agents, and an assignment
alone grants no tool."""
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.db.models import (
    Agent, AgentServiceAssignment, AgentServiceAudit, AgentServiceProvider, AgentServiceResource, CustomMcpConnection,
    CustomMcpToolGrant,
)
from app.routers import custom_mcp
from tests.routers.test_custom_mcp_api import (  # noqa: F401
    PATH, admin, agent_id, app, auth_settings, client, code_of, factory, login, make, owner, pairing_code,
    server, set_owner,
)

API = "/api/v1/integrations/agent-services"


async def named_agent(factory, owner, name) -> int:
    async with factory() as db:
        agent = Agent(owner_id=owner, name=name, instructions="help", provider="claude", approval_mode="ask")
        db.add(agent)
        await db.commit()
        return agent.id


async def provider(admin, name="Mail", kind="agentmail", limit=3):
    conn = await make(admin, name=name)
    put = await admin.put(f"{API}/providers/{conn['id']}", json={"provider": kind, "resource_limit": limit})
    assert put.status_code == 200, put.text
    return conn


async def resource(admin, conn, address="helper@agentmail.to", kind="email", **body):
    return await admin.post(f"{API}/resources", json={
        "connection_id": conn["id"], "kind": kind, "address": address, "label": "Helper inbox", **body})


async def test_a_new_provider_has_no_resources_and_a_new_resource_has_no_agents(admin):
    conn = await provider(admin)
    listed = (await admin.get(f"{API}/providers")).json()
    assert [(p["connection_id"], p["resource_count"], p["resource_limit"]) for p in listed] == [(conn["id"], 0, 3)]
    created = await resource(admin, conn)
    assert created.status_code == 201 and created.json()["agent_ids"] == []


async def test_only_an_existing_connection_can_become_a_provider(admin):
    missing = await admin.put(f"{API}/providers/nope", json={"provider": "agentmail"})
    assert missing.status_code == 404 and code_of(missing) == "connection_not_found"
    assert (await admin.get(f"{API}/providers")).json() == []


async def test_resource_needs_a_provider_and_fits_its_kind(admin):
    plain = await make(admin)
    refused = await resource(admin, plain)
    assert refused.status_code == 404 and code_of(refused) == "provider_not_found"
    mail = await provider(admin)
    wrong = await resource(admin, mail, address="+14155550100", kind="phone")
    assert wrong.status_code == 422 and code_of(wrong) == "kind_not_supported_by_provider"


@pytest.mark.parametrize("kind,address", [
    ("email", "not-an-address"), ("email", "a@b"), ("email", "a b@example.com"),
    ("phone", "4155550100"), ("phone", "+0123456789"), ("phone", "+1 415 555 0100"), ("phone", "+123"),
])
async def test_addresses_must_be_well_formed(admin, kind, address):
    conn = await provider(admin, kind="other")
    refused = await resource(admin, conn, address=address, kind=kind)
    assert refused.status_code == 422 and code_of(refused) == "invalid_address"


async def test_email_is_stored_lowercase_and_not_added_twice(admin):
    conn = await provider(admin)
    first = await resource(admin, conn, address="  Helper@AgentMail.to ")
    assert first.json()["address"] == "helper@agentmail.to"
    again = await resource(admin, conn, address="helper@agentmail.to")
    assert again.status_code == 409 and code_of(again) == "resource_exists"


async def test_the_owner_set_limit_caps_resources_and_cannot_drop_below_what_exists(admin):
    conn = await provider(admin, limit=2)
    assert (await resource(admin, conn, address="a@agentmail.to")).status_code == 201
    assert (await resource(admin, conn, address="b@agentmail.to")).status_code == 201
    full = await resource(admin, conn, address="c@agentmail.to")
    assert full.status_code == 409 and code_of(full) == "resource_limit_reached"
    lower = await admin.put(f"{API}/providers/{conn['id']}", json={"provider": "agentmail", "resource_limit": 1})
    assert lower.status_code == 409 and code_of(lower) == "limit_below_existing"
    raised = await admin.put(f"{API}/providers/{conn['id']}", json={"provider": "agentmail", "resource_limit": 3})
    assert raised.json()["resource_limit"] == 3 and raised.json()["resource_count"] == 2


async def test_a_phone_number_is_kept_in_one_form(admin):
    conn = await provider(admin, name="Phone", kind="agentphone")
    created = await resource(admin, conn, address="+14155550100", kind="phone", provider_resource_id=" num_1 ")
    assert created.status_code == 201
    assert created.json()["address"] == "+14155550100" and created.json()["provider_resource_id"] == "num_1"


async def test_assigning_replaces_the_set_and_an_empty_list_removes_everyone(admin, factory, owner):
    conn = await provider(admin)
    res = (await resource(admin, conn)).json()
    first, second = await named_agent(factory, owner, "One"), await named_agent(factory, owner, "Two")
    both = await admin.put(f"{API}/resources/{res['id']}/agents", json={"agent_ids": [second, first, first]})
    assert both.json()["agent_ids"] == sorted([first, second])
    only = await admin.put(f"{API}/resources/{res['id']}/agents", json={"agent_ids": [second]})
    assert only.json()["agent_ids"] == [second]
    assert [r["id"] for r in (await admin.get(f"{API}/agents/{second}/resources")).json()] == [res["id"]]
    assert (await admin.get(f"{API}/agents/{first}/resources")).json() == []
    none = await admin.put(f"{API}/resources/{res['id']}/agents", json={"agent_ids": []})
    assert none.json()["agent_ids"] == []


async def test_an_assignment_grants_no_tool(admin, factory, owner):
    conn = await provider(admin)
    res = (await resource(admin, conn)).json()
    agent = await agent_id(factory, owner)
    await admin.put(f"{API}/resources/{res['id']}/agents", json={"agent_ids": [agent]})
    async with factory() as db:
        assert (await db.scalars(select(CustomMcpToolGrant))).all() == []


async def test_an_unknown_agent_is_refused_and_changes_nothing(admin, factory, owner):
    conn = await provider(admin)
    res = (await resource(admin, conn)).json()
    agent = await agent_id(factory, owner)
    await admin.put(f"{API}/resources/{res['id']}/agents", json={"agent_ids": [agent]})
    refused = await admin.put(f"{API}/resources/{res['id']}/agents", json={"agent_ids": [agent, 999999999]})
    assert refused.status_code == 404 and code_of(refused) == "agent_not_found"
    assert (await admin.get(f"{API}/resources")).json()[0]["agent_ids"] == [agent]


async def test_deleting_a_resource_removes_its_assignments(admin, factory, owner):
    conn = await provider(admin)
    res = (await resource(admin, conn)).json()
    agent = await agent_id(factory, owner)
    await admin.put(f"{API}/resources/{res['id']}/agents", json={"agent_ids": [agent]})
    assert (await admin.delete(f"{API}/resources/{res['id']}")).status_code == 204
    assert (await admin.get(f"{API}/resources")).json() == []
    async with factory() as db:
        assert (await db.scalars(select(AgentServiceAssignment))).all() == []
    assert (await admin.delete(f"{API}/resources/{res['id']}")).status_code == 404


async def test_removing_the_provider_designation_removes_resources_but_not_the_connection(admin, factory, owner):
    conn = await provider(admin)
    res = (await resource(admin, conn)).json()
    await admin.put(f"{API}/resources/{res['id']}/agents", json={"agent_ids": [await agent_id(factory, owner)]})
    assert (await admin.delete(f"{API}/providers/{conn['id']}")).status_code == 204
    async with factory() as db:
        assert (await db.scalars(select(AgentServiceProvider))).all() == []
        assert (await db.scalars(select(AgentServiceResource))).all() == []
        assert (await db.scalars(select(AgentServiceAssignment))).all() == []
    assert [c["id"] for c in (await admin.get(f"{PATH}/connections")).json()] == [conn["id"]]


async def test_deleting_the_connection_removes_everything_held_under_it(admin, factory, owner):
    conn = await provider(admin)
    res = (await resource(admin, conn)).json()
    await admin.put(f"{API}/resources/{res['id']}/agents", json={"agent_ids": [await agent_id(factory, owner)]})
    assert (await admin.delete(f"{PATH}/connections/{conn['id']}")).status_code == 204
    async with factory() as db:
        assert (await db.scalars(select(AgentServiceProvider))).all() == []
        assert (await db.scalars(select(AgentServiceResource))).all() == []
        assert (await db.scalars(select(AgentServiceAssignment))).all() == []


async def add_audit(factory, owner, connection_id, agent, outcome="completed", **extra):
    async with factory() as db:
        db.add(AgentServiceAudit(
            id=uuid4().hex, owner_id=owner, agent_id=agent, agent_name="Helper", connection_id=connection_id,
            connection_name="Mail", provider="agentmail", tool_name="send_message",
            resources=["helper@agentmail.to"], outcome=outcome, **extra))
        await db.commit()


@pytest.fixture
async def clean_audit(factory, owner):
    yield
    async with factory() as db:
        await db.execute(delete(AgentServiceAudit).where(AgentServiceAudit.owner_id == owner))
        await db.commit()


async def test_the_audit_lists_calls_newest_first_and_can_be_narrowed_to_one_agent(admin, factory, owner, clean_audit):
    conn = await provider(admin)
    one, two = await named_agent(factory, owner, "One"), await named_agent(factory, owner, "Two")
    await add_audit(factory, owner, conn["id"], one, outcome="failed")
    await add_audit(factory, owner, conn["id"], two)
    everything = (await admin.get(f"{API}/audit")).json()
    assert len(everything) == 2 and everything[0]["resources"] == ["helper@agentmail.to"]
    only = (await admin.get(f"{API}/audit", params={"agent_id": one})).json()
    assert [row["outcome"] for row in only] == ["failed"]
    assert len((await admin.get(f"{API}/audit", params={"limit": 1})).json()) == 1
    assert "message" not in " ".join(everything[0]).lower().replace("send_message", "")


async def test_the_audit_survives_removing_the_provider_the_connection_and_the_agent(admin, app, factory, owner, clean_audit):
    conn = await provider(admin)
    agent = await named_agent(factory, owner, "Gone")
    await add_audit(factory, owner, conn["id"], agent)
    assert (await admin.delete(f"{API}/providers/{conn['id']}")).status_code == 204
    assert len((await admin.get(f"{API}/audit")).json()) == 1
    assert (await admin.delete(f"{PATH}/connections/{conn['id']}")).status_code == 204
    async with factory() as db:
        await db.delete(await db.get(Agent, agent))
        await db.commit()
    # With the connection gone nobody can tell whose private one it was, so only the workspace owner reads it.
    async def as_owner():
        custom_mcp._actor_var.set(custom_mcp.Actor(person_id=None))

    app.dependency_overrides[custom_mcp._bind_actor] = as_owner
    try:
        [row] = (await admin.get(f"{API}/audit")).json()
        assert row["agent_name"] == "Helper" and row["connection_name"] == "Mail"
    finally:
        app.dependency_overrides.pop(custom_mcp._bind_actor, None)


async def test_removing_the_provider_designation_also_drops_the_tool_grants_and_stops_running_turns(admin, factory, owner):
    conn = await provider(admin)
    agent = await named_agent(factory, owner, "Granted")
    async with factory() as db:
        db.add(CustomMcpToolGrant(agent_id=agent, connection_id=conn["id"], tool_name="send", schema_digest="d" * 64,
                                  description="", input_schema={"type": "object"}))
        await db.commit()
    assert (await admin.delete(f"{API}/providers/{conn['id']}")).status_code == 204
    async with factory() as db:
        assert (await db.scalars(select(CustomMcpToolGrant))).all() == []
        assert (await db.get(CustomMcpConnection, conn["id"])).revision == conn["revision"] + 1


async def test_the_presets_name_the_official_endpoints(admin):
    presets = {p["preset"]: p for p in (await admin.get(f"{API}/presets")).json()}
    assert presets["agentmail"]["endpoint"] == "https://mcp.agentmail.to/mcp" and presets["agentmail"]["kind"] == "email"
    assert presets["agentphone"]["endpoint"] == "https://mcp.agentphone.ai/mcp" and presets["agentphone"]["kind"] == "phone"
    assert set(presets) == {"agentmail", "agentphone"}


@pytest.mark.parametrize("preset,kind", [("agentmail", "email"), ("agentphone", "phone")])
async def test_a_preset_creates_a_disabled_private_oauth_connection_marked_as_a_provider(admin, preset, kind):
    created = await admin.post(f"{API}/providers", json={"preset": preset, "resource_limit": 2})
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["provider"], body["resource_limit"], body["resource_count"]) == (preset, 2, 0)
    [conn] = (await admin.get(f"{PATH}/connections")).json()
    assert conn["id"] == body["connection_id"] and conn["auth_mode"] == "oauth"
    assert conn["enabled"] is False and conn["shared"] is False and conn["state"] != "connected"
    assert conn["credential_configured"] is False
    address = "helper@agentmail.to" if kind == "email" else "+14155550100"
    made = await admin.post(f"{API}/resources", json={
        "connection_id": body["connection_id"], "kind": kind, "address": address, "label": "First",
        "provider_resource_id": "num_1" if kind == "phone" else None})
    assert made.status_code == 201 and made.json()["agent_ids"] == []


async def test_a_preset_takes_the_given_name_and_a_second_account_is_allowed(admin):
    first = await admin.post(f"{API}/providers", json={"preset": "agentmail", "name": "Work mail"})
    second = await admin.post(f"{API}/providers", json={"preset": "agentmail"})
    assert first.status_code == second.status_code == 201
    assert {p["connection_name"] for p in (await admin.get(f"{API}/providers")).json()} == {"Work mail", "AgentMail"}


async def test_an_unknown_preset_is_refused(admin):
    refused = await admin.post(f"{API}/providers", json={"preset": "telnyx"})
    assert refused.status_code == 422
    assert (await admin.get(f"{PATH}/connections")).json() == []


async def test_an_agentphone_number_needs_its_provider_id_and_an_agentmail_inbox_does_not(admin):
    phone = await provider(admin, name="Phone", kind="agentphone")
    refused = await resource(admin, phone, address="+14155550100", kind="phone")
    assert refused.status_code == 422 and code_of(refused) == "provider_resource_id_required"
    assert (await resource(admin, phone, address="+14155550100", kind="phone", provider_resource_id="  ")).status_code == 422
    assert (await resource(admin, phone, address="+14155550100", kind="phone", provider_resource_id="num_1")).status_code == 201
    mail = await provider(admin, name="Mail2")
    assert (await resource(admin, mail)).status_code == 201


async def test_a_tool_agents_never_get_cannot_be_granted_for_a_phone_provider(admin, factory, owner, server):
    from tests.routers.test_custom_mcp_api import ready, tool as make_tool
    server.tools = [make_tool("buy_number"), make_tool("list_numbers"), make_tool("set_webhook")]
    conn = await ready(admin, name="Phone")
    put = await admin.put(f"{API}/providers/{conn['id']}", json={"provider": "agentphone", "resource_limit": 3})
    assert put.status_code == 200
    agent = await named_agent(factory, owner, "Caller")
    digest = {t["name"]: t["schema_digest"] for t in server.tools}
    grants = f"{PATH}/connections/{conn['id']}/agents/{agent}/grants"
    for name in ("buy_number", "set_webhook"):
        refused = await admin.put(grants, json={"revision": conn["revision"], "tools": [{"name": name, "schema_digest": digest[name]}]})
        assert refused.status_code == 422 and code_of(refused) == "tool_not_allowed_for_agents", name
    allowed = await admin.put(grants, json={"revision": conn["revision"], "tools": [{"name": "list_numbers", "schema_digest": digest["list_numbers"]}]})
    assert allowed.status_code == 200, allowed.text
    # Not a phone provider: the same tool name is an ordinary grant.
    other = await ready(admin, name="Notes")
    ok = await admin.put(f"{PATH}/connections/{other['id']}/agents/{agent}/grants", json={
        "revision": other["revision"], "tools": [{"name": "buy_number", "schema_digest": digest["buy_number"]}]})
    assert ok.status_code == 200, ok.text
