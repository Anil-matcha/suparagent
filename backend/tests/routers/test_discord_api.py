"""Discord connection routes keep tokens write-only and owner-only."""

from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.channels.discord.service import DiscordService
from app.core.config import settings
from app.db.models import Agent
from app.db.models.user_credential import UserCredential
from app.routers import discord as discord_routes
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
