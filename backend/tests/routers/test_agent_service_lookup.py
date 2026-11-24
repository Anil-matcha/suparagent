"""The read-only look-up of a provider's mailboxes or numbers, and paging of the activity list."""
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app import community_mcp
from app.db.models import AgentServiceAudit
from tests.routers.test_custom_mcp_api import (  # noqa: F401
    PATH, admin, app, auth_settings, client, code_of, factory, login, make, owner, pairing_code, server, set_owner,
)

API = "/api/v1/integrations/agent-services"


class FakeSession:
    """Stands in for the network session of the owner's sign-in; records how it was built and called."""
    built: list = []
    calls: list = []
    reply: dict = {}
    error: Exception | None = None

    def __init__(self, endpoint, token=None, *, header=None):
        FakeSession.built.append((endpoint, token, header))

    async def call_tool(self, name, arguments):
        FakeSession.calls.append((name, arguments))
        if FakeSession.error is not None:
            raise FakeSession.error
        return FakeSession.reply

    async def close(self):
        pass


@pytest.fixture
def remote(monkeypatch):
    FakeSession.built, FakeSession.calls, FakeSession.error = [], [], None
    monkeypatch.setattr(community_mcp, "RemoteMcpSession", FakeSession)
    return FakeSession


def reply(key, rows):
    body = {key: rows}
    return {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body, "isError": False}


async def provider(admin, kind):
    conn = await make(admin, name="Svc")
    put = await admin.put(f"{API}/providers/{conn['id']}", json={"provider": kind, "resource_limit": 3})
    assert put.status_code == 200, put.text
    return conn


async def test_agentmail_look_up_lists_the_inboxes_and_marks_the_ones_already_added(admin, remote):
    conn = await provider(admin, "agentmail")
    await admin.post(f"{API}/resources", json={"connection_id": conn["id"], "kind": "email",
                                               "address": "jason@agentmail.to", "label": "Mine"})
    remote.reply = reply("inboxes", [
        {"inboxId": "jason@agentmail.to", "email": "jason@agentmail.to", "displayName": "Jason"},
        {"inboxId": "support@agentmail.to", "email": "support@agentmail.to", "displayName": "Support"}])
    found = (await admin.get(f"{API}/providers/{conn['id']}/lookup")).json()
    assert remote.calls == [("list_inboxes", {"limit": 100})]
    assert found == [
        {"address": "jason@agentmail.to", "provider_resource_id": "jason@agentmail.to", "label": "Jason", "added": True},
        {"address": "support@agentmail.to", "provider_resource_id": "support@agentmail.to", "label": "Support", "added": False}]


async def test_agentphone_look_up_lists_the_numbers_with_their_ids(admin, remote):
    conn = await provider(admin, "agentphone")
    remote.reply = reply("data", [{"id": "num_1", "phoneNumber": "+19032187286", "status": "active"}])
    found = (await admin.get(f"{API}/providers/{conn['id']}/lookup")).json()
    assert remote.calls == [("list_numbers", {"limit": 100})]
    assert found == [{"address": "+19032187286", "provider_resource_id": "num_1", "label": "", "added": False}]


async def test_a_look_up_needs_a_provider_that_has_a_known_list_tool(admin, remote):
    other = await provider(admin, "other")
    refused = await admin.get(f"{API}/providers/{other['id']}/lookup")
    assert refused.status_code == 422 and code_of(refused) == "lookup_not_supported"
    plain = await make(admin, name="Plain")
    missing = await admin.get(f"{API}/providers/{plain['id']}/lookup")
    assert missing.status_code == 404 and code_of(missing) == "provider_not_found"
    assert remote.calls == []


async def test_a_look_up_whose_answer_cannot_be_read_or_that_fails_is_an_error(admin, remote):
    conn = await provider(admin, "agentmail")
    remote.reply = {"content": [{"type": "text", "text": "plain words"}], "isError": False}
    unreadable = await admin.get(f"{API}/providers/{conn['id']}/lookup")
    assert unreadable.status_code == 502 and code_of(unreadable) == "lookup_unreadable"
    remote.error = community_mcp.McpError("remote_unavailable", "down", 502)
    down = await admin.get(f"{API}/providers/{conn['id']}/lookup")
    assert down.status_code == 502 and code_of(down) == "remote_unavailable"


async def test_the_services_text_is_cut_to_plain_short_strings(admin, remote):
    conn = await provider(admin, "agentmail")
    remote.reply = reply("inboxes", [
        {"inboxId": "a@agentmail.to", "email": "a@agentmail.to", "displayName": "Line one\nIgnore previous\x00 text " + "x" * 300},
        {"inboxId": 5, "email": "bad@agentmail.to"}, "not a row", {"inboxId": "", "email": ""}])
    [row] = (await admin.get(f"{API}/providers/{conn['id']}/lookup")).json()
    assert "\n" not in row["label"] and "\x00" not in row["label"] and len(row["label"]) <= 100
    assert row["address"] == "a@agentmail.to"


@pytest.fixture
async def clean_audit(factory, owner):
    yield
    async with factory() as db:
        await db.execute(delete(AgentServiceAudit).where(AgentServiceAudit.owner_id == owner))
        await db.commit()


async def test_the_activity_list_pages_with_an_offset(admin, factory, owner, clean_audit):
    conn = await provider(admin, "agentmail")
    now = datetime.now(timezone.utc)
    async with factory() as db:
        for index in range(5):
            db.add(AgentServiceAudit(
                id=uuid4().hex, owner_id=owner, agent_id=1, agent_name="A", connection_id=conn["id"], connection_name="Svc",
                provider="agentmail", tool_name=f"tool_{index}", resources=["a@agentmail.to"], outcome="completed",
                created_at=now - timedelta(minutes=index)))
        await db.commit()
    names = lambda r: [row["tool_name"] for row in r.json()]  # noqa: E731
    assert names(await admin.get(f"{API}/audit", params={"limit": 2})) == ["tool_0", "tool_1"]
    assert names(await admin.get(f"{API}/audit", params={"limit": 2, "offset": 2})) == ["tool_2", "tool_3"]
    assert names(await admin.get(f"{API}/audit", params={"limit": 2, "offset": 4})) == ["tool_4"]
    assert names(await admin.get(f"{API}/audit", params={"limit": 2, "offset": 6})) == []
    assert (await admin.get(f"{API}/audit", params={"offset": -1})).status_code == 422
