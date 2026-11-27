"""Tests for extended per-agent settings: overview, prompt preview, tool scope,
tools_off, outbound policy, reply style, check_work verification, and cache keep-warm overrides.
"""

import random
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete

from app.auth.policy import Principal
from app.core.config import settings
from app.db.models import Agent
from app.routers.agents import get_runtime as agents_runtime
from app.routers.agents import router as agents_router
from app.services import agent_memory, agent_skills, composio_policy, tool_policy, tool_scope
from app.services.cache_keep_warm import Candidate, KeepWarm
from app.services.provider_adapter import TurnContext, TurnOutcome
from tests.services.thread_harness import cleanup, cli, finished, owner, rt, sessions  # noqa: F401


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


@pytest.fixture(autouse=True)
async def agent_cleanup(sessions, owner):
    yield
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


class _AsPrincipal:
    def __init__(self, app, principal):
        self.app, self.principal = app, principal

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.principal is not None:
            scope.setdefault("state", {})["auth"] = self.principal
        await self.app(scope, receive, send)


def _app_for(rt, *, principal=None) -> FastAPI:
    app = FastAPI()
    app.include_router(agents_router, prefix="/api/v1")
    app.dependency_overrides[agents_runtime] = lambda: rt
    if principal is not None:
        app.add_middleware(_AsPrincipal, principal=principal)
    return app


@pytest.fixture
async def api(rt, cli):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(_app_for(rt), client=("127.0.0.1", 50000)),
        base_url="http://localhost",
    ) as client:
        yield client


async def _create_agent(api, **body) -> dict:
    body.setdefault("name", "Agent " + uuid4().hex[:6])
    res = await api.post("/api/v1/agents", json=body)
    assert res.status_code == 201, res.text
    return res.json()


async def test_overview_and_prompt_preview(api):
    agent = await _create_agent(
        api,
        title="Release coordinator",
        instructions="Check all unit tests before merging.",
        reply_style="conversational",
        tool_scope={"allow": ["native:Read"], "deny": ["native:Bash"]},
    )
    agent_id = agent["id"]
    agent_memory.write_memory(agent_id, "Staging API token sk-secretvalue1234567890abcdef1234\n")
    agent_skills.write_skill(
        agent_id,
        "release-check",
        "Pre-release checklist",
        "Always inspect the changelog first.",
        enabled=True,
    )

    ov = await api.get(f"/api/v1/agents/{agent_id}/overview")
    assert ov.status_code == 200, ov.text
    ov_data = ov.json()
    assert ov_data["who"]["name"] == agent["name"]
    assert ov_data["summary"]["skills_count"] == 1
    assert any(item["id"] == "instructions" and item["done"] is True for item in ov_data["setup"])

    preview = await api.get(f"/api/v1/agents/{agent_id}/prompt-preview")
    assert preview.status_code == 200, preview.text
    pv = preview.json()
    ids = [sec["id"] for sec in pv["sections"]]
    assert {"instructions", "reply_style", "memory", "skills", "tool_scope"}.issubset(ids)
    mem_sec = next(sec for sec in pv["sections"] if sec["id"] == "memory")
    assert "sk-secretvalue1234567890abcdef1234" not in mem_sec["text"]
    assert pv["total_bytes"] > 0
    assert pv["approx_tokens"] > 0


async def test_settings_round_trip_and_validation(api):
    checker = await _create_agent(api, name="Verifier " + uuid4().hex[:4])
    worker = await _create_agent(
        api,
        tool_scope={"allow": ["native:*", "mcp:gmail:*"], "deny": ["native:Bash"]},
        tools_off=["images", "speech"],
        outbound_policy="allow",
        reply_style="conversational",
        check_work={"enabled": True, "checker_agent_id": checker["id"]},
        cache_keep_warm=True,
    )
    assert worker["tool_scope"] == {"allow": ["native:*", "mcp:gmail:*"], "deny": ["native:Bash"]}
    assert worker["tools_off"] == ["images", "speech"]
    assert worker["outbound_policy"] == "allow"
    assert worker["reply_style"] == "conversational"
    assert worker["check_work"] == {"enabled": True, "checker_agent_id": checker["id"]}
    assert worker["cache_keep_warm"] is True

    bad_scope = await api.patch(f"/api/v1/agents/{worker['id']}", json={"tool_scope": {"allow": ["not valid!"]}})
    assert bad_scope.status_code == 422

    bad_group = await api.patch(f"/api/v1/agents/{worker['id']}", json={"tools_off": ["unknown_group"]})
    assert bad_group.status_code == 422

    self_check = await api.patch(
        f"/api/v1/agents/{worker['id']}",
        json={"check_work": {"enabled": True, "checker_agent_id": worker["id"]}},
    )
    assert self_check.status_code == 422
    assert self_check.json()["detail"]["error_code"] == "invalid_check_work"


async def test_manage_scope_blocks_security_settings(rt, cli):
    manage_principal = Principal(kind="device", scopes=("chat", "manage"), session_id=1, label="Phone")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(_app_for(rt, principal=manage_principal), client=("127.0.0.1", 50000)),
        base_url="http://localhost",
    ) as manage_api:
        res_scope = await manage_api.post(
            "/api/v1/agents",
            json={"name": "Managed " + uuid4().hex[:5], "tool_scope": {"allow": ["native:Read"]}},
        )
        assert res_scope.status_code == 403

        res_outbound = await manage_api.post(
            "/api/v1/agents",
            json={"name": "Managed " + uuid4().hex[:5], "outbound_policy": "allow"},
        )
        assert res_outbound.status_code == 403

        res_check = await manage_api.post(
            "/api/v1/agents",
            json={"name": "Managed " + uuid4().hex[:5], "check_work": {"enabled": False, "checker_agent_id": None}},
        )
        assert res_check.status_code == 403


def test_tool_scope_and_tools_off_policy():
    assert tool_scope.tool_allowed({"allow": ["native:*", "mcp:gmail:*"], "deny": ["native:Bash"]}, "native:Read") is True
    assert tool_scope.tool_allowed({"allow": ["native:*"], "deny": ["native:Bash"]}, "native:Bash") is False
    assert tool_scope.tool_allowed({"allow": ["mcp:gmail:*"]}, "mcp:slack:post_message") is False

    run = SimpleNamespace(
        ctx=SimpleNamespace(
            media_review_only=False,
            draft_only=False,
            composio_apps={"gmail": "write"},
            composio_first_use_hit=False,
            composio_auto_calls=set(),
        ),
        tool_scope={"allow": ["native:Read", "mcp:gmail:*"], "deny": ["native:Bash"]},
        tools_off=["memory_tools", "computers"],
        outbound_policy="ask",
        routine_grants=None,
        uninterrupted_access=False,
    )
    assert tool_policy.automatic("native_dummy", "ask", run, {})[1] == "tool_scope_denied"
    assert tool_policy.automatic("mcp__memory__session_search", "ask", run, {}) == ("denied", "tool_group_disabled")
    assert tool_policy.automatic("mcp__computer__screenshot", "ask", run, {}) == ("denied", "tool_group_disabled")


def test_outbound_policy_in_composio_policy():
    levels = {"gmail": "write"}
    assert composio_policy.automatic("mcp__composio__gmail_send_email", "ask", levels, outbound_policy="ask") is None
    assert composio_policy.automatic("mcp__composio__gmail_send_email", "ask", levels, outbound_policy="allow") == (
        "allowed",
        "outbound_policy_allow",
    )
    assert composio_policy.automatic("mcp__composio__gmail_send_email", "plan", levels, outbound_policy="allow") == (
        "denied",
        "plan_mode",
    )


def test_reply_style_turn_instructions(tmp_path, monkeypatch):
    agent = SimpleNamespace(
        id=999991,
        instructions="Always be helpful.",
        reply_style="conversational",
        memory_enabled=False,
    )
    monkeypatch.setattr("app.services.about_me.prompt_block", lambda: "")
    monkeypatch.setattr("app.services.business_profile.prompt_block", lambda: "")
    text = agent_memory.turn_instructions(agent)
    assert text is not None
    assert "conversational style" in text


@pytest.mark.asyncio
async def test_cache_keep_warm_per_agent_override():
    now = 1_000_000.0
    pinged: list[int] = []
    global_flag = False
    rows = [
        Candidate(1, "claude", now - 200, agent_keep_warm=True),
        Candidate(2, "claude", now - 200, agent_keep_warm=False),
        Candidate(3, "claude", now - 200, agent_keep_warm=None),
    ]

    async def list_candidates(since: float, limit: int):
        return rows

    async def send_ping(thread_id: int):
        pinged.append(thread_id)
        return now + 1

    kw = KeepWarm(
        enabled=lambda: global_flag,
        candidates=list_candidates,
        send=send_ping,
        busy=lambda _tid: False,
        clock=lambda: now,
        rng=random.Random(0),
    )
    # Initial tick registers candidates with real_at = now - 200; advance clock by 100s so gap (<= 240s) is elapsed
    await kw.tick()
    now += 100.0
    await kw.tick()
    assert pinged == [1]

    pinged.clear()
    global_flag = True
    now += 300.0
    await kw.tick()
    assert set(pinged) == {1, 3}
    assert 2 not in pinged


@pytest.mark.asyncio
async def test_check_work_emits_single_review_event(rt, sessions, owner, monkeypatch):
    async with sessions() as db:
        checker = Agent(owner_id=owner, name="Checker " + uuid4().hex[:4], provider="claude", model="haiku")
        db.add(checker)
        await db.commit()
        await db.refresh(checker)

    class FakeAdapter:
        async def generate_text(self, prompt: str, **kwargs) -> str:
            assert "User request" in prompt
            return "Verified: response covers all requested steps."

    monkeypatch.setitem(rt.adapters, "claude", FakeAdapter())
    monkeypatch.setattr(rt, "prepare_adapter", lambda *_args, **_kwargs: _async_none())

    emitted: list[tuple[str, dict]] = []

    class FakeRecorder:
        lost = False

        async def emit(self, event_type: str, data: dict):
            emitted.append((event_type, data))
            return True

    run = SimpleNamespace(
        thread_id=1,
        turn_id=1,
        agent_id=checker.id + 100,
        prompt_text="Summarize the release notes.",
        check_work={"enabled": True, "checker_agent_id": checker.id},
        recorder=FakeRecorder(),
        ctx=TurnContext(
            thread_id=1,
            turn_id=1,
            prompt="hi",
            workspace=None,
            model=None,
            provider_session_id=None,
            provider_state=None,
            resume=False,
            history=lambda: _async_empty(),
        ),
    )
    await rt._run_check_work(run, TurnOutcome("succeeded", result="Here is the summary."))
    assert len(emitted) == 1
    assert emitted[0][0] == "check_work.reviewed"
    assert emitted[0][1]["checker_agent_id"] == checker.id
    assert "Verified:" in emitted[0][1]["note"]


async def _async_none():
    return None


async def _async_empty():
    return []
