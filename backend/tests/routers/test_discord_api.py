"""Discord connection routes keep tokens write-only and owner-only."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.channels.discord.api import DiscordAuthError
from app.channels.discord.service import DiscordService
from app.core.config import settings
from app.db.models import Agent
from app.db.models.user_credential import UserCredential
from app.routers import discord as discord_routes
from app.services import agent_store
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.auth.test_auth_api import (  # noqa: F401
    app, factory, auth_settings, client, login, pairing_code, set_owner,
)
from tests.channels.discord.test_discord import FakeApi, FakeClient, FakeLock, TOKEN

PATH = "/api/v1/integrations/discord"


@pytest.fixture
async def owner_id(factory):
    owner = "od_discord_" + uuid4().hex
    yield owner
    async with factory() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


@pytest.fixture
def discord(app, factory, owner_id, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    runtime = ThreadRuntime(hub=ThreadHub(), adapters={}, status_ttl=0, _session_factory=factory, owner_id=owner_id)
    service = DiscordService(runtime, api_factory=FakeApi, client_factory=FakeClient, lock=FakeLock())
    app.dependency_overrides[discord_routes.get_discord_service] = lambda: service
    return service


@pytest.fixture
async def admin(app, factory, discord):
    await set_owner(factory)
    async with client(app) as api:
        session = await login(api)
        api.headers["X-CSRF-Token"] = session["csrf_token"]
        yield api
    await discord.stop()


async def test_token_is_write_only_and_link_code_is_separate(admin):
    assert (await admin.get(PATH)).json()["configured"] is False
    saved = await admin.put(f"{PATH}/credentials", json={"bot_token": TOKEN})
    assert saved.status_code == 200, saved.text
    assert saved.json()["configured"] is True
    assert TOKEN not in saved.text
    assert saved.headers["cache-control"] == "no-store"
    code = await admin.post(f"{PATH}/link-code")
    assert code.status_code == 200 and len(code.json()["code"]) == 6
    assert code.json()["code"] not in (await admin.get(PATH)).text
    removed = await admin.delete(PATH)
    assert removed.status_code == 204
    assert (await admin.get(PATH)).json()["configured"] is False


@pytest.mark.parametrize("body", [{}, {"bot_token": TOKEN, "extra": "x"}, {"bot_token": 123},
                                   {"bot_token": "bad\ntoken"}])
async def test_bad_token_body_never_echoes_it(admin, body):
    response = await admin.put(f"{PATH}/credentials", json=body)
    assert response.status_code == 422
    assert TOKEN not in response.text and "bad\ntoken" not in response.text


def code_of(response) -> str:
    return response.json()["detail"]["error_code"]


async def saved_rows(factory, owner):
    async with factory() as db:
        return (await db.execute(select(UserCredential).where(UserCredential.user_id == owner))).scalars().all()


async def test_the_token_is_stored_encrypted(admin, factory, owner_id):
    await admin.put(f"{PATH}/credentials", json={"bot_token": TOKEN})
    (row,) = await saved_rows(factory, owner_id)
    assert row.provider == "integration:discord" and TOKEN not in row.ciphertext


async def test_a_token_discord_refuses_is_not_saved_and_not_echoed(app, factory, owner_id, monkeypatch, admin):
    def refusing(token):
        api = FakeApi(token)
        api.failure = DiscordAuthError("http_401")
        return api

    service = app.dependency_overrides[discord_routes.get_discord_service]()
    service._api_factory = refusing
    response = await admin.put(f"{PATH}/credentials", json={"bot_token": TOKEN})
    assert response.status_code == 422 and code_of(response) == "discord_refused_token"
    assert TOKEN not in response.text
    assert await saved_rows(factory, owner_id) == []


async def test_a_link_code_needs_a_connection_first(admin):
    refused = await admin.post(f"{PATH}/link-code")
    assert refused.status_code == 409 and code_of(refused) == "not_connected"


async def test_the_agent_is_chosen_from_the_owners_agents(admin, factory, owner_id, tmp_path):
    await admin.put(f"{PATH}/credentials", json={"bot_token": TOKEN})
    missing = await admin.put(f"{PATH}/agent", json={"agent_id": 99999999})
    assert missing.status_code == 422 and code_of(missing) == "agent_unavailable"
    for bad in ({"agent_id": 0}, {"agent_id": "x"}, {}, {"agent_id": 1, "x": 1}):
        assert (await admin.put(f"{PATH}/agent", json=bad)).status_code == 422
    async with factory() as db:
        agent = await agent_store.create_agent(db, owner_id=owner_id, name="Helper", provider="claude", workspace_path=str(tmp_path))
    chosen = await admin.put(f"{PATH}/agent", json={"agent_id": agent.id})
    assert chosen.status_code == 200 and chosen.json()["agent_id"] == agent.id


async def test_a_paired_chat_device_and_signed_out_callers_are_refused(app, factory, discord, owner_id):
    await set_owner(factory)
    async with client(app) as anonymous:
        assert (await anonymous.get(PATH)).status_code == 401
    async with client(app) as owner_client, client(app) as phone:
        session = await login(owner_client)
        code = await pairing_code(owner_client, session["csrf_token"], scope="chat")
        token = (await phone.post("/api/v1/auth/pair", json={"code": code})).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        for method, path, body in (
            ("GET", PATH, None), ("PUT", f"{PATH}/credentials", {"bot_token": TOKEN}), ("POST", f"{PATH}/link-code", None),
            ("PUT", f"{PATH}/agent", {"agent_id": 1}), ("DELETE", PATH, None),
        ):
            response = await phone.request(method, path, headers=headers, json=body)
            assert response.status_code == 403, (method, path, response.text)
    assert await saved_rows(factory, owner_id) == []


async def test_changes_need_the_csrf_token(app, factory, discord, owner_id):
    await set_owner(factory)
    async with client(app) as api:
        await login(api)
        assert (await api.put(f"{PATH}/credentials", json={"bot_token": TOKEN})).status_code == 403
        assert (await api.post(f"{PATH}/link-code")).status_code == 403
        assert (await api.delete(PATH)).status_code == 403
    assert await saved_rows(factory, owner_id) == []


async def test_a_body_that_is_not_json_or_too_large_is_refused(admin):
    assert (await admin.put(f"{PATH}/credentials", content=b"not json")).status_code == 422
    big = b'{"bot_token": "' + b"a" * 5000 + b'"}'
    response = await admin.put(f"{PATH}/credentials", content=big, headers={"content-type": "application/json"})
    assert response.status_code == 422 and "aaaa" not in response.text
