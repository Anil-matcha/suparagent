"""The LLMTR preset of the OpenAI-compatible engine, against a fake
endpoint (httpx MockTransport; nothing reaches llmtr.com): its default
address, required key and its own saved configuration."""

import json

import httpx
import pytest
from sqlalchemy import delete

from app.core.config import settings
from app.db.models import Agent
from app.db.models.user_credential import UserCredential
from app.services import engine_config, local_claude_runner
from app.services.openai_chat_adapter import (
    LLMTR_BASE_URL,
    InvalidConfig,
    LLMTRAdapter,
    OpenAIChatAdapter,
)
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ProviderUnavailable, ThreadRuntime
from tests.services.test_openai_chat_adapter import Endpoint, data_of
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401

KEY = "sk-llmtr-FAKE-0123456789abcdef"
MODEL = "minimax/minimax-m3.1-flash"


@pytest.fixture
def endpoint():
    return Endpoint()


@pytest.fixture
async def lrt(sessions, owner, monkeypatch, endpoint, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    runtime = ThreadRuntime(
        hub=ThreadHub(),
        adapters={
            "openai_compat": OpenAIChatAdapter(transport=httpx.MockTransport(endpoint)),
            "llmtr": LLMTRAdapter(transport=httpx.MockTransport(endpoint)),
        },
        status_ttl=0, _session_factory=sessions, owner_id=owner,
    )
    yield runtime
    await runtime.shutdown()
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


def test_config_defaults_the_address_and_requires_a_key():
    adapter = LLMTRAdapter()
    saved = adapter.validate_config({"api_key": KEY, "model": MODEL}, None)
    assert saved == {"base_url": LLMTR_BASE_URL, "model": MODEL, "api_key": KEY}
    assert adapter.validate_config({}, saved)["api_key"] == KEY
    assert adapter.public_config(saved) == {"base_url": LLMTR_BASE_URL, "model": MODEL, "has_api_key": True}
    for payload in ({}, {"api_key": "  "}, {"base_url": "https://proxy.test/v1", "api_key": KEY}, {"clear_api_key": True}):
        with pytest.raises(InvalidConfig):
            adapter.validate_config(payload, None)


async def test_without_a_key_it_is_not_ready_and_sends_nothing(endpoint):
    adapter = LLMTRAdapter(transport=httpx.MockTransport(endpoint))
    for config in (None, {"base_url": LLMTR_BASE_URL}):
        adapter.config = config
        status, probe = await adapter.status(), await adapter.probe()
        assert not status.ready and "LLMTR API key" in status.error
        assert not probe.ready and "LLMTR API key" in probe.reason
    assert endpoint.requests == []


async def test_probe_lists_llmtr_models(endpoint):
    endpoint.handler = lambda r: httpx.Response(200, json={"data": [{"id": "qwen/qwen3.8-27b-free"}, {"id": MODEL}]})
    adapter = LLMTRAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": LLMTR_BASE_URL, "api_key": KEY, "model": MODEL}
    probe = await adapter.probe()
    assert probe.ready and probe.signed_in is True
    assert [(m.id, m.default) for m in probe.models] == [("qwen/qwen3.8-27b-free", False), (MODEL, True)]
    request = endpoint.requests[0]
    assert str(request.url) == "https://llmtr.com/v1/models"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert "x-title" not in request.headers and "http-referer" not in request.headers


async def test_invalid_key_is_not_ready(endpoint):
    endpoint.handler = lambda r: httpx.Response(401, json={"error": {"message": KEY}})
    adapter = LLMTRAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": LLMTR_BASE_URL, "api_key": KEY}
    probe = await adapter.probe()
    assert not probe.ready and probe.signed_in is False and KEY not in probe.reason
    assert len(endpoint.requests) == 1


async def test_old_proxy_config_never_receives_the_key(endpoint):
    adapter = LLMTRAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": "https://proxy.test/v1", "api_key": KEY}
    assert not (await adapter.probe()).ready
    assert not (await adapter.status()).ready
    assert endpoint.requests == []


async def test_catalog_error_is_not_ready(endpoint):
    endpoint.handler = lambda r: httpx.Response(500, json={})
    adapter = LLMTRAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": LLMTR_BASE_URL, "api_key": KEY}
    assert not (await adapter.probe()).ready


async def test_unreadable_catalog_keeps_a_saved_model(endpoint):
    endpoint.handler = lambda r: httpx.Response(200, text="not json")
    adapter = LLMTRAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": LLMTR_BASE_URL, "api_key": KEY, "model": MODEL}
    probe = await adapter.probe()
    assert probe.ready and probe.signed_in is True
    assert [(m.id, m.default) for m in probe.models] == [(MODEL, True)]


async def test_a_turn_goes_to_llmtr_with_its_own_saved_config(lrt, sessions, owner, endpoint):
    async with sessions() as db:
        await engine_config.save(db, owner, "llmtr", LLMTRAdapter().validate_config({"api_key": KEY, "model": MODEL}, None))
        await engine_config.save(db, owner, "openai_compat", {"base_url": "http://llm.test/v1", "model": "local"})
    thread = await lrt.create_thread(provider="llmtr", model=MODEL)
    await lrt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    events = await events_of(sessions, thread.id)
    assert data_of(events, "assistant.message") == [{"text": "Hello"}]
    request = next(r for r in endpoint.requests if r.method == "POST")
    assert str(request.url) == "https://llmtr.com/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert "x-title" not in request.headers
    assert json.loads(request.content)["model"] == MODEL
    other = await lrt.create_thread(provider="openai_compat")
    await lrt.send_turn(other.id, "hi")
    await finished(sessions, other.id)
    local = endpoint.requests[-1]
    assert str(local.url) == "http://llm.test/v1/chat/completions"
    assert "authorization" not in local.headers


async def test_a_turn_without_a_saved_key_is_refused(lrt, sessions, owner, endpoint):
    thread = await lrt.create_thread(provider="llmtr", model=MODEL)
    with pytest.raises(ProviderUnavailable, match="LLMTR"):
        await lrt.send_turn(thread.id, "hi")
    assert endpoint.requests == []
