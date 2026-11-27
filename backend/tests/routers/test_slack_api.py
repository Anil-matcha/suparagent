"""/api/v1/integrations/slack through the real auth middleware and routes, with a
fake Slack behind the service (no network, no WebSocket)."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.channels.slack.api import SlackAuthError
from app.channels.slack.service import SlackService
from app.core.config import settings
from app.db.models import Agent
from app.db.models.user_credential import UserCredential
from app.routers import slack as slack_routes
from app.services import agent_store
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.auth.test_auth_api import (  # noqa: F401
    app, factory, auth_settings, client, login, pairing_code, set_owner,
)
from tests.channels.slack.test_service import APP, BOT, FakeApi, FakeClient, FakeLock

PATH = "/api/v1/integrations/slack"
BODY = {"bot_token": BOT, "app_token": APP}


@pytest.fixture
async def owner_id(factory):
    owner = "od_slack_" + uuid4().hex
    yield owner
    async with factory() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


@pytest.fixture
def slack(app, factory, owner_id, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)

    class Slack:
        apis: list = []
        refuse = False

    fake = Slack()

    def make_api(bot, app_token):
        api = FakeApi(bot, app_token)
        if fake.refuse:
            api.auth_error = SlackAuthError("invalid_auth")
        fake.apis.append(api)
        return api

    runtime = ThreadRuntime(hub=ThreadHub(), adapters={}, status_ttl=0, _session_factory=factory, owner_id=owner_id)
    fake.apis = []
    fake.service = SlackService(runtime, api_factory=make_api, client_factory=FakeClient, lock=FakeLock(), supervise_seconds=3600)
    app.dependency_overrides[slack_routes.get_slack_service] = lambda: fake.service
    return fake


@pytest.fixture
async def admin(app, factory, slack):
    await set_owner(factory)
    async with client(app) as api:
        session = await login(api)
        api.headers["X-CSRF-Token"] = session["csrf_token"]
        yield api
    await slack.service.stop()


def code_of(response) -> str:
    return response.json()["detail"]["error_code"]


async def stored(factory, owner):
    async with factory() as db:
        return (await db.execute(select(UserCredential).where(UserCredential.user_id == owner))).scalars().all()


async def test_connecting_saves_encrypted_tokens_and_never_returns_them(admin, factory, owner_id):
    assert (await admin.get(PATH)).json() == {"available": True, "configured": False, "state": "not_configured", "detail": None}
    saved = await admin.put(f"{PATH}/credentials", json=BODY)
    assert saved.status_code == 200, saved.text
    assert saved.headers["cache-control"] == "no-store"
    body = saved.json()
    assert body["configured"] is True and body["team"] == "Acme" and body["linked"] is False
    for text in (saved.text, (await admin.get(PATH)).text):
        assert BOT not in text and APP not in text
    (row,) = await stored(factory, owner_id)
    assert row.provider == "integration:slack" and BOT not in row.ciphertext and APP not in row.ciphertext


@pytest.mark.parametrize("body", [
    {"bot_token": "nope", "app_token": APP},
    {"bot_token": BOT, "app_token": "xoxb" + "-not-an-app-token"},
    {"bot_token": APP, "app_token": BOT},
])
async def test_a_wrong_token_is_refused_without_echoing_it(admin, slack, body):
    response = await admin.put(f"{PATH}/credentials", json=body)
    assert response.status_code == 422
    assert code_of(response) in {"invalid_bot_token", "invalid_app_token", "tokens_swapped"}
    assert "nope" not in response.text and BOT not in response.text and APP not in response.text
    assert slack.apis == []


@pytest.mark.parametrize("body", [{"bot_token": BOT}, {}, {**BODY, "extra": 1}, {"bot_token": 1, "app_token": 2}, ["x"], "x"])
async def test_a_malformed_body_is_refused(admin, slack, body):
    response = await admin.put(f"{PATH}/credentials", json=body)
    assert response.status_code == 422 and code_of(response) == "invalid_slack_tokens"
    assert BOT not in response.text and APP not in response.text
    assert slack.apis == []


async def test_a_body_that_is_not_json_or_too_large_is_refused(admin, slack):
    assert (await admin.put(f"{PATH}/credentials", content=b"not json")).status_code == 422
    big = json.dumps({"bot_token": "xoxb-" + "a" * 5000, "app_token": APP}).encode()
    response = await admin.put(f"{PATH}/credentials", content=big, headers={"content-type": "application/json"})
    assert response.status_code == 422 and "aaaa" not in response.text
    assert slack.apis == []


async def test_tokens_slack_refuses_are_not_saved(admin, slack, factory, owner_id):
    slack.refuse = True
    response = await admin.put(f"{PATH}/credentials", json=BODY)
    assert response.status_code == 422 and code_of(response) == "slack_refused_token"
    assert BOT not in response.text and APP not in response.text
    assert await stored(factory, owner_id) == []


async def test_a_link_code_needs_a_connection_then_is_six_digits_and_not_shown_again_in_status(admin):
    refused = await admin.post(f"{PATH}/link-code")
    assert refused.status_code == 409 and code_of(refused) == "not_connected"
    await admin.put(f"{PATH}/credentials", json=BODY)
    made = await admin.post(f"{PATH}/link-code")
    assert made.status_code == 201 or made.status_code == 200
    assert made.headers["cache-control"] == "no-store"
    code = made.json()["code"]
    assert len(code) == 6 and code.isdigit() and made.json()["expires_in"] == 600
    status = await admin.get(PATH)
    assert status.json()["link_code_active"] is True and code not in status.text


async def test_the_agent_is_chosen_from_the_owners_agents(admin, factory, owner_id, tmp_path):
    await admin.put(f"{PATH}/credentials", json=BODY)
    missing = await admin.put(f"{PATH}/agent", json={"agent_id": 99999999})
    assert missing.status_code == 422 and code_of(missing) == "agent_unavailable"
    for bad in ({"agent_id": 0}, {"agent_id": "x"}, {}, {"agent_id": 1, "x": 1}):
        assert (await admin.put(f"{PATH}/agent", json=bad)).status_code == 422
    async with factory() as db:
        agent = await agent_store.create_agent(db, owner_id=owner_id, name="Helper", provider="claude", workspace_path=str(tmp_path))
    chosen = await admin.put(f"{PATH}/agent", json={"agent_id": agent.id})
    assert chosen.status_code == 200 and chosen.json()["agent_id"] == agent.id


async def test_disconnect_forgets_the_connection(admin, factory, owner_id):
    await admin.put(f"{PATH}/credentials", json=BODY)
    assert (await admin.delete(PATH)).status_code == 204
    assert (await admin.get(PATH)).json()["configured"] is False
    assert await stored(factory, owner_id) == []


async def test_a_paired_chat_device_and_signed_out_callers_are_refused(app, factory, slack):
    await set_owner(factory)
    async with client(app) as anonymous:
        assert (await anonymous.get(PATH)).status_code == 401
    async with client(app) as owner_client, client(app) as phone:
        session = await login(owner_client)
        code = await pairing_code(owner_client, session["csrf_token"], scope="chat")
        token = (await phone.post("/api/v1/auth/pair", json={"code": code})).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        for method, path, body in (
            ("GET", PATH, None), ("PUT", f"{PATH}/credentials", BODY), ("POST", f"{PATH}/link-code", None),
            ("PUT", f"{PATH}/agent", {"agent_id": 1}), ("DELETE", PATH, None),
        ):
            response = await phone.request(method, path, headers=headers, json=body)
            assert response.status_code == 403, (method, path, response.text)
    assert slack.apis == []


async def test_changes_need_the_csrf_token(app, factory, slack):
    await set_owner(factory)
    async with client(app) as api:
        await login(api)
        assert (await api.put(f"{PATH}/credentials", json=BODY)).status_code == 403
        assert (await api.post(f"{PATH}/link-code")).status_code == 403
        assert (await api.delete(PATH)).status_code == 403
    assert slack.apis == []
