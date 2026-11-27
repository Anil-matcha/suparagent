"""GET /api/v1/agents/{id}/shadow-report: owner-scoped, admin or owner only, exact shape. The numbers themselves are
tested in tests/services/test_shadow_report.py."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import delete

from app.db.models import Agent, ShadowPrediction
from app.routers.agents import get_runtime as agents_runtime
from app.routers.agents import router as agents_router
from app.routers.shadow import router as shadow_router
from app.services import agent_store, shadow_report
from tests.routers.test_agents_api import agent_cleanup, client_for, code_of, local_enabled  # noqa: F401
from tests.routers.test_agents_manage_api import ADMIN, PHONE, AsPrincipal, made
from tests.services.thread_harness import cleanup, cli, owner, rt, sessions  # noqa: F401

SHAPE = {"agent_id", "window_days", "generated_at", "sample", "ranking", "drafts", "disagreements", "accounts"}


def app_as(rt, principal=None) -> FastAPI:
    app = FastAPI()
    app.include_router(agents_router, prefix="/api/v1")
    app.include_router(shadow_router, prefix="/api/v1")
    app.dependency_overrides[agents_runtime] = lambda: rt
    app.add_middleware(AsPrincipal, principal=principal)
    return app


@pytest.fixture(autouse=True)
def clean_cache():
    shadow_report._CACHE.clear()
    shadow_report._REFRESHED.clear()


@pytest.fixture
async def owner_client(rt, cli):
    async with client_for(app_as(rt)) as client:
        yield client


@pytest.fixture
async def admin_client(rt, cli):
    async with client_for(app_as(rt, ADMIN)) as client:
        yield client


@pytest.fixture
async def phone_client(rt, cli):
    async with client_for(app_as(rt, PHONE)) as client:
        yield client


def url(agent_id, query=""):
    return f"/api/v1/agents/{agent_id}/shadow-report{query}"


async def test_the_owner_and_an_admin_get_the_exact_shape(owner_client, admin_client):
    agent = await made(owner_client)
    for client in (owner_client, admin_client):
        response = await client.get(url(agent["id"]))
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == SHAPE and body["agent_id"] == agent["id"] and body["window_days"] == 28
        assert body["sample"] == {"predictions": 0, "with_owner_reply": 0, "drafts": 0}
        assert body["ranking"] == {"top5_answered_24h": {"rate": None, "n": 0},
                                   "baseline_answered_24h": {"rate": None, "n": 0}, "too_few": True}
        assert body["drafts"] == {"as_is": 0, "light_edit": 0, "rewrite": 0, "no_reply_seen": 0, "usable_rate": None, "too_few": True}
        assert body["disagreements"] == [] and body["accounts"] == []
        datetime.fromisoformat(body["generated_at"])


async def test_another_owners_agent_is_a_404(owner_client, sessions):
    async with sessions() as db:
        theirs = await agent_store.create_agent(db, owner_id="someone-else-" + uuid4().hex, name="X")
    try:
        assert (await owner_client.get(url(theirs.id))).status_code == 404
        assert (await owner_client.get(url(987654321))).status_code == 404
    finally:
        async with sessions() as db:
            await db.execute(delete(Agent).where(Agent.id == theirs.id))
            await db.commit()


async def test_a_phone_without_admin_access_is_refused(owner_client, phone_client):
    agent = await made(owner_client)
    response = await phone_client.get(url(agent["id"]))
    assert response.status_code == 403 and code_of(response) == "shadow_report_requires_admin"


@pytest.mark.parametrize("query", ["?days=0", "?days=91", "?days=abc", "?refresh=maybe"])
async def test_bad_query_values_are_422(owner_client, query):
    agent = await made(owner_client)
    assert (await owner_client.get(url(agent["id"], query))).status_code == 422


async def test_days_and_refresh_reach_the_report(owner_client, monkeypatch):
    agent = await made(owner_client)
    seen = []

    async def fake_build(rt, agent_id, days, *, refresh=False, now=None):
        seen.append((agent_id, days, refresh))
        return {"agent_id": agent_id, "window_days": days, "generated_at": datetime.now(UTC).isoformat(),
                "sample": {"predictions": 1, "with_owner_reply": 1, "drafts": 0},
                "ranking": {"top5_answered_24h": {"rate": None, "n": 0}, "baseline_answered_24h": {"rate": None, "n": 0}, "too_few": True},
                "drafts": {"as_is": 0, "light_edit": 0, "rewrite": 0, "no_reply_seen": 0, "usable_rate": None, "too_few": True},
                "disagreements": [{"thread_id": "t1", "kind": "ranked_high_unanswered", "rank": 1, "category": "bug",
                                   "predicted_at": datetime.now(UTC).isoformat(), "draft_text": "must be dropped"}],
                "accounts": ["ca_a"], "secret_extra": "must be dropped"}

    monkeypatch.setattr(shadow_report, "build", fake_build)
    body = (await owner_client.get(url(agent["id"], "?days=7&refresh=true"))).json()
    assert seen == [(agent["id"], 7, True)] and body["window_days"] == 7
    assert "secret_extra" not in body and "draft_text" not in body["disagreements"][0]  # the response model is the whole shape


def test_the_route_needs_the_admin_scope_before_the_handler_even_runs():
    from app.auth.policy import required_scope

    assert required_scope("GET", "/agents/12/shadow-report") == "admin"
