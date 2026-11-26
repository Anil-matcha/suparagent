"""The AI/ML API preset of the OpenAI-compatible engine, against a fake
endpoint (httpx MockTransport; nothing reaches api.aimlapi.com): its fixed
address, required key, chat-only model catalog, default model, and that the
key and source header go to api.aimlapi.com only."""

import json

import httpx
import pytest
from sqlalchemy import delete

from app.core.config import settings
from app.db.models import Agent
from app.db.models.user_credential import UserCredential
from app.services import engine_config, local_claude_runner
from app.services.openai_chat_adapter import (
    AIMLAPI_BASE_URL,
    AIMLAPI_DEFAULT_MODEL,
    AimlapiAdapter,
    InvalidConfig,
    OpenAIChatAdapter,
)
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ProviderUnavailable, ThreadRuntime
from tests.services.test_openai_chat_adapter import Endpoint, data_of
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401

KEY = "aiml-FAKE-0123456789abcdef"
MODEL = "deepseek/deepseek-chat"
CATALOG = [
    {"id": "openai/gpt-4o-mini", "type": "openai/chat-completions"},
    {"id": MODEL, "type": "openai/chat-completions"},
    {"id": "flux/schnell", "type": "openai/image-generations"},
    {"id": "anthropic/claude-x", "type": "anthropic/messages"},
]


def answering(request: httpx.Request) -> httpx.Response | None:
    if request.url.path.endswith("/billing/balance"):
        return httpx.Response(200, json={"balance": 1})
    if request.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": CATALOG})
    return None


@pytest.fixture
def endpoint():
    return Endpoint()


@pytest.fixture
async def ort(sessions, owner, monkeypatch, endpoint, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    runtime = ThreadRuntime(
        hub=ThreadHub(),
        adapters={
            "openai_compat": OpenAIChatAdapter(transport=httpx.MockTransport(endpoint)),
            "aimlapi": AimlapiAdapter(transport=httpx.MockTransport(endpoint)),
        },
        status_ttl=0, _session_factory=sessions, owner_id=owner,
    )
    yield runtime
    await runtime.shutdown()
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


def test_config_fixes_the_address_and_requires_a_key():
    adapter = AimlapiAdapter()
    saved = adapter.validate_config({"api_key": KEY, "model": MODEL}, None)
    assert saved == {"base_url": AIMLAPI_BASE_URL, "model": MODEL, "api_key": KEY}
    assert adapter.validate_config({}, saved)["api_key"] == KEY
    assert adapter.public_config(saved) == {"base_url": AIMLAPI_BASE_URL, "model": MODEL, "has_api_key": True}
    for payload in ({}, {"api_key": "  "}, {"base_url": "https://evil.test/v1", "api_key": KEY},
                    {"base_url": "https://api.aimlapi.com.evil.test/v1", "api_key": KEY}, {"clear_api_key": True}):
        with pytest.raises(InvalidConfig):
            adapter.validate_config(payload, None)


async def test_without_a_key_it_is_not_ready_and_sends_nothing(endpoint):
    adapter = AimlapiAdapter(transport=httpx.MockTransport(endpoint))
    for config in (None, {"base_url": AIMLAPI_BASE_URL}):
        adapter.config = config
        status, probe = await adapter.status(), await adapter.probe()
        assert not status.ready and "AI/ML API key" in status.error
        assert not probe.ready and "AI/ML API key" in probe.reason
    assert endpoint.requests == []


async def test_probe_lists_only_chat_models_with_the_default_and_headers(endpoint):
    endpoint.handler = answering
    adapter = AimlapiAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": AIMLAPI_BASE_URL, "api_key": KEY}
    probe = await adapter.probe()
    assert probe.ready and probe.signed_in is True
    assert [(m.id, m.default) for m in probe.models] == [(MODEL, False), (AIMLAPI_DEFAULT_MODEL, True)]
    assert [str(r.url) for r in endpoint.requests] == [
        "https://api.aimlapi.com/v1/billing/balance", "https://api.aimlapi.com/v1/models",
    ]
    for request in endpoint.requests:
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.headers["x-aimlapi-source"] == "suparagent"


async def test_invalid_key_is_signed_out_without_showing_the_body(endpoint):
    endpoint.handler = lambda r: httpx.Response(401, json={"error": {"message": KEY}})
    adapter = AimlapiAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": AIMLAPI_BASE_URL, "api_key": KEY}
    probe = await adapter.probe()
    assert not probe.ready and probe.signed_in is False and KEY not in probe.reason
    assert len(endpoint.requests) == 1


async def test_a_foreign_host_never_receives_the_key_or_header(endpoint):
    adapter = AimlapiAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": "https://api.aimlapi.com.evil.test/v1", "api_key": KEY}
    assert not (await adapter.probe()).ready
    assert not (await adapter.status()).ready
    assert endpoint.requests == []
    for base in ("https://evil.test/v1", "https://api.aimlapi.com.evil.test/v1", "http://aimlapi.com/v1"):
        headers = adapter._headers({"base_url": base, "api_key": KEY})
        assert "Authorization" not in headers and "X-AIMLAPI-Source" not in headers


async def test_a_redirect_is_not_followed_with_the_key(endpoint):
    endpoint.handler = lambda r: (
        httpx.Response(302, headers={"location": "https://evil.test/steal"}) if r.url.host == "api.aimlapi.com" else httpx.Response(200, json={})
    )
    adapter = AimlapiAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": AIMLAPI_BASE_URL, "api_key": KEY}
    assert not (await adapter.probe()).ready
    assert {r.url.host for r in endpoint.requests} == {"api.aimlapi.com"}


async def test_a_turn_goes_to_aimlapi_with_the_default_model(ort, sessions, owner, endpoint):
    async with sessions() as db:
        await engine_config.save(db, owner, "aimlapi", AimlapiAdapter().validate_config({"api_key": KEY}, None))
        await engine_config.save(db, owner, "openai_compat", {"base_url": "http://llm.test/v1", "model": "local"})
    previous = endpoint.handler
    endpoint.handler = lambda r: answering(r) or previous(r)
    thread = await ort.create_thread(provider="aimlapi")
    await ort.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    events = await events_of(sessions, thread.id)
    assert data_of(events, "assistant.message") == [{"text": "Hello"}]
    request = next(r for r in endpoint.requests if r.method == "POST")
    assert str(request.url) == "https://api.aimlapi.com/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}" and request.headers["x-aimlapi-source"] == "suparagent"
    assert json.loads(request.content)["model"] == AIMLAPI_DEFAULT_MODEL
    assert KEY not in json.dumps(events, default=str)
    # The plain OpenAI-compatible engine keeps its own address and gets nothing of ours.
    other = await ort.create_thread(provider="openai_compat")
    await ort.send_turn(other.id, "hi")
    await finished(sessions, other.id)
    local = endpoint.requests[-1]
    assert str(local.url) == "http://llm.test/v1/chat/completions"
    assert "x-aimlapi-source" not in local.headers and "authorization" not in local.headers


async def test_a_turn_without_a_saved_key_is_refused(ort, sessions, owner, endpoint):
    thread = await ort.create_thread(provider="aimlapi", model=MODEL)
    with pytest.raises(ProviderUnavailable, match="AI/ML API key"):
        await ort.send_turn(thread.id, "hi")
    assert endpoint.requests == []
