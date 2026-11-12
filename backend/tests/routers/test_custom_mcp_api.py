"""/api/v1/integrations/custom-mcp: drafts, tests, enabling, secrets and exact grants.

The remote server is faked at the discovery boundary; the SSRF/transport rules are covered
by the shared client tests."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app import community_mcp as mcp
from app.core.config import settings
from app.db.models import Agent, CustomMcpConnection, CustomMcpToolGrant, UserCredential
from app.routers import custom_mcp
from tests.auth.test_auth_api import (  # noqa: F401
    app, factory, auth_settings, client, login, pairing_code, set_owner,
)

PATH = "/api/v1/integrations/custom-mcp"
SECRET = "sk_live_SECRET_custom_value"
URL = "https://tools.example.com/mcp"


def tool(name="search", extra=None):
    schema = {"type": "object", "properties": {"q": {"type": "string"}, **(extra or {})}}
    return mcp._normalize_tool({"name": name, "description": f"{name} tool", "inputSchema": schema})


class FakeServer:
    def __init__(self):
        self.tools = [tool("search"), tool("write_note")]
        self.error: mcp.McpError | None = None
        self.identity: dict | None = {"name": "Fake Notes", "version": "1.2.3"}
        self.seen: list[tuple[str, str | None, str | None]] = []

    async def discover(self, endpoint, token, header):
        self.seen.append((endpoint, token, header))
        if self.error is not None:
            raise self.error
        return mcp.reject_credential_in_metadata(list(self.tools), token), self.identity


@pytest.fixture
async def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(custom_mcp, "_discover", fake.discover)
    return fake


@pytest.fixture
async def owner(factory, monkeypatch):
    owner = "od_custom_" + uuid4().hex
    monkeypatch.setattr(custom_mcp, "LOCAL_OWNER_ID", owner)
    yield owner
    async with factory() as db:
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.execute(delete(CustomMcpConnection).where(CustomMcpConnection.owner_id == owner))
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


@pytest.fixture
async def admin(app, factory, server, owner, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    await set_owner(factory)
    async with client(app) as api:
        session = await login(api)
        api.headers["X-CSRF-Token"] = session["csrf_token"]
        yield api


def code_of(response) -> str:
    return response.json()["detail"]["error_code"]


async def make(admin, **body):
    payload = {"name": "Notes", "endpoint": URL, **body}
    created = await admin.post(f"{PATH}/connections", json=payload)
    assert created.status_code == 201, created.text
    return created.json()


async def ready(admin, **body):
    """A connection that was tested and enabled; returns its latest view."""
    conn = await make(admin, **body)
    tested = await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": conn["revision"]})
    assert tested.status_code == 200, tested.text
    enabled = await admin.post(f"{PATH}/connections/{conn['id']}/enable", json={"revision": tested.json()["revision"]})
    assert enabled.status_code == 200, enabled.text
    return enabled.json()


async def agent_id(factory, owner) -> int:
    async with factory() as db:
        agent = Agent(owner_id=owner, name="Helper", instructions="help", provider="claude", approval_mode="ask")
        db.add(agent)
        await db.commit()
        return agent.id


async def test_a_new_connection_is_a_disabled_draft_with_no_secret_in_the_response(admin, factory, owner):
    conn = await make(admin, auth_mode="bearer", credential=SECRET)
    assert conn["enabled"] is False and conn["state"] == "not_tested"
    assert conn["credential_configured"] is True and conn["tool_count"] is None
    assert SECRET not in json.dumps(conn)
    assert SECRET not in (await admin.get(f"{PATH}/connections")).text
    async with factory() as db:
        row = (await db.execute(select(UserCredential).where(UserCredential.user_id == owner))).scalar_one()
        assert SECRET not in row.ciphertext


async def test_two_connections_to_one_service_stay_independent(admin):
    first, second = await make(admin, name="A"), await make(admin, name="B")
    assert first["id"] != second["id"]
    await admin.delete(f"{PATH}/connections/{first['id']}")
    assert [c["id"] for c in (await admin.get(f"{PATH}/connections")).json()] == [second["id"]]


@pytest.mark.parametrize("endpoint", [
    "http://tools.example.com/mcp", "https://localhost/mcp", "https://10.0.0.5/mcp",
    "https://169.254.169.254/latest", "https://u:p@tools.example.com/mcp",
    "https://tools.example.com/mcp?key=abc", "https://tools.example.com:8443/mcp",
])
async def test_unsafe_endpoints_are_refused_at_save_time(admin, endpoint):
    refused = await admin.post(f"{PATH}/connections", json={"name": "X", "endpoint": endpoint})
    assert refused.status_code == 400 and code_of(refused) == "unsafe_endpoint"


@pytest.mark.parametrize("header", [None, "", "Authorization", "Host", "Mcp-Session-Id", "bad name", "X\r\nEvil"])
async def test_header_auth_refuses_reserved_or_malformed_header_names(admin, header):
    refused = await admin.post(f"{PATH}/connections", json={
        "name": "X", "endpoint": URL, "auth_mode": "header", "auth_header_name": header, "credential": SECRET})
    assert refused.status_code == 422 and code_of(refused) == "invalid_header_name"


@pytest.mark.parametrize("secret", ["line\r\nbreak", "tab\tvalue", "ünïcode", "x" * 4097])
async def test_a_secret_that_could_inject_headers_is_refused_without_echo(admin, secret):
    refused = await admin.post(f"{PATH}/connections", json={
        "name": "X", "endpoint": URL, "auth_mode": "bearer", "credential": secret})
    assert refused.status_code == 422 and code_of(refused) == "invalid_credential"
    assert secret not in refused.text


async def test_a_secret_needs_an_auth_mode_that_uses_it(admin):
    refused = await admin.post(f"{PATH}/connections", json={"name": "X", "endpoint": URL, "credential": SECRET})
    assert refused.status_code == 422 and code_of(refused) == "credential_not_supported"


async def test_testing_discovers_tools_and_sends_the_secret_in_the_chosen_place(admin, server):
    conn = await make(admin, auth_mode="header", auth_header_name="X-Api-Key", credential=SECRET)
    tested = await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": conn["revision"]})
    body = tested.json()
    assert body["state"] == "disabled" and body["tool_count"] == 2 and body["enabled"] is False
    assert server.seen == [(URL, SECRET, "X-Api-Key")]
    names = [t["name"] for t in (await admin.get(f"{PATH}/connections/{conn['id']}/tools")).json()]
    assert names == ["search", "write_note"]


async def test_bearer_mode_sends_no_custom_header_and_none_sends_no_secret(admin, server):
    bearer = await make(admin, auth_mode="bearer", credential=SECRET)
    await admin.post(f"{PATH}/connections/{bearer['id']}/test", json={"revision": bearer["revision"]})
    plain = await make(admin, name="Plain")
    await admin.post(f"{PATH}/connections/{plain['id']}/test", json={"revision": plain["revision"]})
    assert server.seen == [(URL, SECRET, None), (URL, None, None)]


async def test_testing_without_the_needed_secret_is_refused_before_any_request(admin, server):
    conn = await make(admin, auth_mode="bearer")
    refused = await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": conn["revision"]})
    assert refused.status_code == 409 and code_of(refused) == "credential_required"
    assert server.seen == []


async def test_a_failed_test_leaves_the_connection_disabled_with_a_state(admin, server):
    conn = await ready(admin)
    server.error = mcp.McpError("authentication_failed", "rejected", 502)
    failed = await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": conn["revision"]})
    assert failed.status_code == 502 and code_of(failed) == "authentication_failed"
    current = (await admin.get(f"{PATH}/connections")).json()[0]
    assert current["enabled"] is False and current["state"] == "needs_sign_in" and current["tool_count"] is None
    server.error = mcp.McpError("connect_failed", "down", 502)
    again = await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": current["revision"]})
    assert again.status_code == 502
    assert (await admin.get(f"{PATH}/connections")).json()[0]["state"] == "unreachable"


async def test_a_tool_list_echoing_the_secret_is_rejected(admin, server):
    server.tools = [mcp._normalize_tool({"name": "leak", "description": f"key {SECRET}", "inputSchema": {"type": "object"}})]
    conn = await make(admin, auth_mode="bearer", credential=SECRET)
    refused = await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": conn["revision"]})
    assert refused.status_code == 502 and code_of(refused) == "unsafe_metadata"
    assert SECRET not in refused.text


async def test_enabling_needs_a_current_successful_test(admin):
    conn = await make(admin)
    refused = await admin.post(f"{PATH}/connections/{conn['id']}/enable", json={"revision": conn["revision"]})
    assert refused.status_code == 409 and code_of(refused) == "connection_not_tested"
    assert (await admin.get(f"{PATH}/connections/{conn['id']}/tools")).status_code == 409


async def test_a_stale_settings_view_cannot_overwrite_a_newer_change(admin):
    conn = await ready(admin)
    changed = await admin.patch(f"{PATH}/connections/{conn['id']}", json={
        "revision": conn["revision"], "endpoint": "https://other.example.com/mcp"})
    assert changed.status_code == 200
    stale = await admin.patch(f"{PATH}/connections/{conn['id']}", json={
        "revision": conn["revision"], "endpoint": URL})
    assert stale.status_code == 409 and code_of(stale) == "connection_changed"
    for action in ("test", "enable", "disable"):
        late = await admin.post(f"{PATH}/connections/{conn['id']}/{action}", json={"revision": conn["revision"]})
        assert late.status_code == 409


async def test_changing_the_endpoint_disables_drops_grants_and_the_secret(admin, factory, owner):
    conn = await ready(admin, auth_mode="bearer", credential=SECRET)
    agent = await agent_id(factory, owner)
    granted = await admin.put(f"{PATH}/connections/{conn['id']}/agents/{agent}/grants", json={
        "revision": conn["revision"], "tools": [{"name": "search", "schema_digest": tool("search")["schema_digest"]}]})
    assert granted.status_code == 200
    moved = await admin.patch(f"{PATH}/connections/{conn['id']}", json={
        "revision": conn["revision"], "endpoint": "https://elsewhere.example.com/mcp"})
    body = moved.json()
    assert body["enabled"] is False and body["state"] == "not_tested" and body["credential_configured"] is False
    assert (await admin.get(f"{PATH}/connections/{conn['id']}/agents/{agent}/grants")).json() == {"tool_names": []}


async def test_rotating_the_secret_requires_a_new_test_and_a_blank_secret_keeps_it(admin, server):
    conn = await ready(admin, auth_mode="bearer", credential=SECRET)
    kept = await admin.patch(f"{PATH}/connections/{conn['id']}", json={
        "revision": conn["revision"], "name": "Renamed", "credential": ""})
    assert kept.json()["credential_configured"] is True and kept.json()["state"] == "connected"
    rotated = await admin.patch(f"{PATH}/connections/{conn['id']}", json={
        "revision": kept.json()["revision"], "credential": "sk_new_secret"})
    assert rotated.json()["state"] == "not_tested" and rotated.json()["enabled"] is False
    tested = await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": rotated.json()["revision"]})
    assert tested.status_code == 200 and server.seen[-1][1] == "sk_new_secret"


async def test_removing_the_secret_disables_and_clears_it(admin, factory, owner):
    conn = await ready(admin, auth_mode="bearer", credential=SECRET)
    removed = await admin.delete(f"{PATH}/connections/{conn['id']}/credential", params={"revision": conn["revision"]})
    assert removed.json()["credential_configured"] is False and removed.json()["enabled"] is False
    async with factory() as db:
        assert (await db.execute(select(UserCredential).where(UserCredential.user_id == owner))).first() is None


async def test_new_connections_start_with_no_grants_and_grants_need_an_enabled_connection(admin, factory, owner):
    agent = await agent_id(factory, owner)
    draft = await make(admin)
    refused = await admin.put(f"{PATH}/connections/{draft['id']}/agents/{agent}/grants", json={
        "revision": draft["revision"], "tools": []})
    assert refused.status_code == 409 and code_of(refused) == "connection_not_enabled"
    conn = await ready(admin)
    assert (await admin.get(f"{PATH}/connections/{conn['id']}/agents/{agent}/grants")).json() == {"tool_names": []}


async def test_grants_are_exact_and_follow_the_schema_digest(admin, factory, owner, server):
    conn = await ready(admin)
    agent = await agent_id(factory, owner)
    digest = tool("search")["schema_digest"]
    path = f"{PATH}/connections/{conn['id']}/agents/{agent}/grants"
    wrong = await admin.put(path, json={"revision": conn["revision"], "tools": [{"name": "search", "schema_digest": "0" * 64}]})
    assert wrong.status_code == 409 and code_of(wrong) == "tool_schema_changed"
    unknown = await admin.put(path, json={"revision": conn["revision"], "tools": [{"name": "nope", "schema_digest": digest}]})
    assert unknown.status_code == 409
    dupes = await admin.put(path, json={"revision": conn["revision"], "tools": [{"name": "search", "schema_digest": digest}] * 2})
    assert dupes.status_code == 422
    ok = await admin.put(path, json={"revision": conn["revision"], "tools": [{"name": "search", "schema_digest": digest}]})
    assert ok.json() == {"tool_names": ["search"]}
    # The server changes the tool: re-testing removes the now-stale grant.
    server.tools = [tool("search", {"extra": {"type": "string"}}), tool("write_note")]
    latest = (await admin.get(f"{PATH}/connections")).json()[0]
    retest = await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": latest["revision"]})
    assert retest.status_code == 200
    assert (await admin.get(path)).json() == {"tool_names": []}


async def test_another_owners_agent_and_connection_are_not_reachable(admin, factory, owner):
    other = "od_other_" + uuid4().hex
    async with factory() as db:
        foreign = Agent(owner_id=other, name="Theirs", instructions="x", provider="claude", approval_mode="ask")
        db.add(foreign)
        row = CustomMcpConnection(id=str(uuid4()), owner_id=other, name="Theirs", endpoint=URL)
        db.add(row)
        await db.commit()
        foreign_agent, foreign_conn = foreign.id, row.id
    try:
        conn = await ready(admin)
        assert (await admin.get(f"{PATH}/connections/{conn['id']}/agents/{foreign_agent}/grants")).status_code == 404
        for method, suffix, body in (("post", "test", {"revision": 1}), ("delete", "", None)):
            call = getattr(admin, method)
            url = f"{PATH}/connections/{foreign_conn}" + (f"/{suffix}" if suffix else "")
            response = await call(url, json=body) if body else await call(url)
            assert response.status_code == 404
        assert foreign_conn not in [c["id"] for c in (await admin.get(f"{PATH}/connections")).json()]
    finally:
        async with factory() as db:
            await db.execute(delete(CustomMcpConnection).where(CustomMcpConnection.owner_id == other))
            await db.execute(delete(Agent).where(Agent.owner_id == other))
            await db.commit()


async def test_deleting_removes_the_secret_and_every_grant(admin, factory, owner):
    conn = await ready(admin, auth_mode="bearer", credential=SECRET)
    agent = await agent_id(factory, owner)
    await admin.put(f"{PATH}/connections/{conn['id']}/agents/{agent}/grants", json={
        "revision": conn["revision"], "tools": [{"name": "search", "schema_digest": tool("search")["schema_digest"]}]})
    assert (await admin.delete(f"{PATH}/connections/{conn['id']}")).status_code == 204
    async with factory() as db:
        assert (await db.execute(select(UserCredential).where(UserCredential.user_id == owner))).first() is None
        assert (await db.execute(select(CustomMcpToolGrant).where(CustomMcpToolGrant.connection_id == conn["id"]))).first() is None
        assert (await db.execute(select(CustomMcpConnection).where(CustomMcpConnection.id == conn["id"]))).first() is None


async def test_signed_out_callers_and_missing_csrf_are_refused(app, factory, server, owner, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    await set_owner(factory)
    async with client(app) as api:
        assert (await api.get(f"{PATH}/connections")).status_code in (401, 403)
        await login(api)
        assert (await api.post(f"{PATH}/connections", json={"name": "X", "endpoint": URL})).status_code == 403


async def test_the_server_identity_is_shown_after_a_test_and_cleared_when_the_target_changes(admin, server):
    conn = await make(admin)
    assert conn["server_name"] is None
    tested = (await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": conn["revision"]})).json()
    assert (tested["server_name"], tested["server_version"]) == ("Fake Notes", "1.2.3")
    moved = await admin.patch(f"{PATH}/connections/{conn['id']}", json={
        "revision": tested["revision"], "endpoint": "https://other.example.com/mcp"})
    assert moved.json()["server_name"] is None and moved.json()["server_version"] is None


async def test_a_server_that_names_nothing_leaves_the_identity_empty_and_a_failed_test_clears_it(admin, server):
    server.identity = None
    conn = await make(admin)
    tested = (await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": conn["revision"]})).json()
    assert tested["server_name"] is None
    server.identity = {"name": "Named", "version": None}
    again = (await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": tested["revision"]})).json()
    assert again["server_name"] == "Named" and again["server_version"] is None
    server.error = mcp.McpError("connect_failed", "down", 502)
    await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": again["revision"]})
    assert (await admin.get(f"{PATH}/connections")).json()[0]["server_name"] is None
