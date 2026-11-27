"""The MuAPI chat preset of the OpenAI-compatible engine, against a fake
endpoint (httpx MockTransport; nothing reaches muapi.ai): its split routes,
the key it takes from Settings -> Integrations, the model order and what it
refuses without a key."""

import json

import httpx
import pytest
from sqlalchemy import delete

from app.core.config import settings
from app.db.models import Agent
from app.db.models.user_credential import UserCredential
from app.services import engine_config, local_claude_runner, user_credential_service
from app.services.openai_chat_adapter import (
    MUAPI_CHAT_BASE_URL,
    MUAPI_CREDENTIAL,
    MUAPI_MODELS_URL,
    MuapiChatAdapter,
    OpenAIChatAdapter,
)
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ProviderUnavailable, ThreadRuntime
from tests.services.test_openai_chat_adapter import Endpoint, data_of
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401

KEY = "muapi-FAKE-0123456789abcdef"
MODELS = {"data": [{"id": "qwen-3-8-27b-abliterated"}, {"id": "gpt-5-6-sol"}, {"id": "gpt-5-6-luna"}, {"id": "cydonia-24b-v4-1"}]}


@pytest.fixture
def endpoint():
    return Endpoint()


@pytest.fixture
async def mrt(sessions, owner, monkeypatch, endpoint, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    runtime = ThreadRuntime(
        hub=ThreadHub(),
        adapters={
            "openai_compat": OpenAIChatAdapter(transport=httpx.MockTransport(endpoint)),
            "muapi": MuapiChatAdapter(transport=httpx.MockTransport(endpoint)),
        },
        status_ttl=0, _session_factory=sessions, owner_id=owner,
    )
    yield runtime
    await runtime.shutdown()
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


async def save_integration_key(sessions, owner, key=KEY):
    async with sessions() as db:
        await user_credential_service.save_token(
            db, user_id=owner, provider=MUAPI_CREDENTIAL, file_path="-", plaintext=json.dumps(key),
        )


def test_the_setup_and_fields_fit_the_engine_list_schema():
    from app.schemas.engine import EngineConfigField, EngineSetup

    EngineSetup(**MuapiChatAdapter.setup)
    for field in MuapiChatAdapter.config_fields:
        EngineConfigField(**field)


def test_config_keeps_only_the_model_and_never_a_key_or_address():
    adapter = MuapiChatAdapter()
    saved = adapter.validate_config(
        {"model": "gpt-5-6-luna", "api_key": KEY, "base_url": "https://evil.test/v1"}, {"api_key": "old"}
    )
    assert saved == {"base_url": MUAPI_CHAT_BASE_URL, "model": "gpt-5-6-luna"}
    assert KEY not in json.dumps(saved)
    assert adapter.public_config({"model": "x", "api_key": KEY}) == {
        "base_url": MUAPI_CHAT_BASE_URL, "model": "x", "has_api_key": True,
    }
    assert adapter.public_config(None)["has_api_key"] is False


async def test_without_a_key_it_is_not_ready_and_sends_nothing(endpoint):
    adapter = MuapiChatAdapter(transport=httpx.MockTransport(endpoint))
    for config in (None, {"base_url": MUAPI_CHAT_BASE_URL}):
        adapter.config = config
        status, probe = await adapter.status(), await adapter.probe()
        assert not status.ready and "Settings → Integrations" in status.error
        assert not probe.ready and "Settings → Integrations" in probe.reason
    assert endpoint.requests == []


async def test_probe_lists_gpt_models_first_and_marks_community_ones(endpoint):
    endpoint.handler = lambda r: httpx.Response(200, json=MODELS)
    adapter = MuapiChatAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": MUAPI_CHAT_BASE_URL, "api_key": KEY}
    probe = await adapter.probe()
    assert probe.ready and probe.signed_in is True
    assert [m.id for m in probe.models] == ["gpt-5-6-luna", "gpt-5-6-sol", "cydonia-24b-v4-1", "qwen-3-8-27b-abliterated"]
    assert [m.id for m in probe.models if m.default] == ["gpt-5-6-sol"]
    assert [m.label for m in probe.models][2:] == ["cydonia-24b-v4-1 (community)", "qwen-3-8-27b-abliterated (community)"]
    request = endpoint.requests[0]
    assert str(request.url) == MUAPI_MODELS_URL
    assert request.headers["authorization"] == f"Bearer {KEY}" and request.headers["x-api-key"] == KEY


async def test_a_saved_default_model_wins(endpoint):
    endpoint.handler = lambda r: httpx.Response(200, json=MODELS)
    adapter = MuapiChatAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": MUAPI_CHAT_BASE_URL, "api_key": KEY, "model": "gpt-5-6-luna"}
    assert [m.id for m in (await adapter.probe()).models if m.default] == ["gpt-5-6-luna"]


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_key_is_not_ready_and_is_not_echoed(endpoint, status):
    endpoint.handler = lambda r: httpx.Response(status, json={"detail": KEY})
    adapter = MuapiChatAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": MUAPI_CHAT_BASE_URL, "api_key": KEY}
    probe = await adapter.probe()
    assert not probe.ready and probe.signed_in is False and KEY not in probe.reason


@pytest.mark.parametrize("status,body", [(500, {}), (404, {}), (200, "not json")])
async def test_a_failed_model_list_does_not_report_ready_or_models(endpoint, status, body):
    endpoint.handler = lambda r: httpx.Response(status, json=body)
    adapter = MuapiChatAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": MUAPI_CHAT_BASE_URL, "api_key": KEY}
    probe = await adapter.probe()
    assert not probe.ready or not probe.models


async def test_the_key_is_only_sent_to_muapi(endpoint):
    adapter = MuapiChatAdapter(transport=httpx.MockTransport(endpoint))
    assert "authorization" not in {k.lower() for k in adapter._headers({"api_key": KEY, "base_url": "https://proxy.test/v1"})}
    assert adapter._headers({"api_key": KEY, "base_url": MUAPI_CHAT_BASE_URL})["x-api-key"] == KEY


async def test_a_turn_uses_the_integrations_key_and_chat_route(mrt, sessions, owner, endpoint):
    await save_integration_key(sessions, owner)
    thread = await mrt.create_thread(provider="muapi", model="gpt-5-6-luna")
    await mrt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    events = await events_of(sessions, thread.id)
    assert data_of(events, "assistant.message") == [{"text": "Hello"}]
    request = next(r for r in endpoint.requests if r.method == "POST")
    assert str(request.url) == "https://api.muapi.ai/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content)["model"] == "gpt-5-6-luna"
    assert KEY not in json.dumps(events, default=str)
    system = json.loads(request.content)["messages"][0]
    assert system["role"] == "system" and "plain Markdown" in system["content"]
    # The key was read, not copied: this engine has no saved configuration of its own.
    async with sessions() as db:
        assert await engine_config.load(db, owner, "muapi") is None


async def test_a_turn_without_the_integrations_key_is_refused(mrt, sessions, owner, endpoint):
    thread = await mrt.create_thread(provider="muapi", model="gpt-5-6-luna")
    with pytest.raises(ProviderUnavailable, match="Settings → Integrations"):
        await mrt.send_turn(thread.id, "hi")
    assert endpoint.requests == []
