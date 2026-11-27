"""POST/GET /agents/{id}/shadow-feedback and GET /agents/{id}/share-suggestion: owner scoped, admin or owner only, exact shapes.
The numbers and rules are tested in tests/services/test_shadow_feedback.py and test_share_suggestion.py."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select

from app import gmail_share
from app.db.models import Agent, ShadowFeedback, ShadowPrediction
from app.routers.agents import get_runtime as agents_runtime
from app.routers.agents import router as agents_router
from app.routers.shadow import router as shadow_router
from app.services import agent_store, share_suggestion
from tests.routers.test_agents_api import agent_cleanup, client_for, code_of, local_enabled  # noqa: F401
from tests.routers.test_agents_manage_api import ADMIN, PHONE, AsPrincipal, made
from tests.services.test_composio_gmail_share import ACCOUNT, entry, rule
from tests.services.thread_harness import cleanup, cli, owner, rt, sessions  # noqa: F401

SUGGESTION_SHAPE = {"agent_id", "state", "account_id", "current_percent", "suggested_percent", "evidence", "reason"}
EVIDENCE_SHAPE = {"evaluated", "usable", "usable_rate", "lower", "upper", "days", "wrong_policy"}


def app_as(rt, principal=None) -> FastAPI:
    app = FastAPI()
    app.include_router(agents_router, prefix="/api/v1")
    app.include_router(shadow_router, prefix="/api/v1")
    app.dependency_overrides[agents_runtime] = lambda: rt
    app.add_middleware(AsPrincipal, principal=principal)
    return app


@pytest.fixture(autouse=True)
async def clean(sessions, owner):
    share_suggestion._CACHE.clear()
    share_suggestion._REFRESHED.clear()
    yield
    async with sessions() as db:
        await db.execute(delete(ShadowFeedback).where(ShadowFeedback.owner_id == owner))
        await db.execute(delete(ShadowPrediction).where(ShadowPrediction.owner_id == owner))
        await db.commit()


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


def feedback_url(agent_id, query=""):
    return f"/api/v1/agents/{agent_id}/shadow-feedback{query}"


def suggestion_url(agent_id, query=""):
    return f"/api/v1/agents/{agent_id}/share-suggestion{query}"


async def worker_with_a_prediction(owner_client, sessions, owner, percent=50):
    agent = await made(owner_client)
    async with sessions() as db:
        await agent_store.update_agent(db, agent["id"], connector_scopes={"gmail": "read"}, connector_account_scopes={"gmail": [ACCOUNT]})
        (await db.get(Agent, agent["id"])).connector_shares = {"gmail": {ACCOUNT: entry(percent)}}
        thread = next(f"th{n}" for n in range(100_000) if gmail_share.in_share(rule(percent), f"th{n}"))
        db.add(ShadowPrediction(owner_id=owner, agent_id=agent["id"], account_id=ACCOUNT, thread_id=thread, rank=1, category="bug",
                                draft_text="a draft", turn_id=1, predicted_at=datetime.now(UTC) - timedelta(days=2)))
        await db.commit()
    return agent, thread


GOOD = {"kind": "draft", "reason": "wrong_tone"}


async def test_feedback_is_recorded_and_replaced(owner_client, admin_client, sessions, owner):
    agent, thread = await worker_with_a_prediction(owner_client, sessions, owner)
    for client, reason in ((owner_client, "wrong_tone"), (admin_client, "missing_context")):
        response = await client.post(feedback_url(agent["id"]), json={"thread_id": thread, **GOOD, "reason": reason, "note": "ok"})
        assert response.status_code == 200, response.text
        assert response.json() == {"thread_id": thread, "kind": "draft", "reason": reason, "recorded": True}
    async with sessions() as db:
        (row,) = (await db.execute(select(ShadowFeedback))).scalars().all()
    assert row.reason == "missing_context"


@pytest.mark.parametrize("extra", [
    {"reason": "boring"}, {"kind": "label"}, {"note": "x" * 301}, {"thread_id": ""}, {"draft_text": "must be refused"}, {"owner_id": "x"},
])
async def test_bad_bodies_are_422_and_store_nothing(owner_client, sessions, owner, extra):
    agent, thread = await worker_with_a_prediction(owner_client, sessions, owner)
    response = await owner_client.post(feedback_url(agent["id"]), json={"thread_id": thread, **GOOD, **extra})
    assert response.status_code == 422
    async with sessions() as db:
        assert (await db.execute(select(ShadowFeedback))).scalars().all() == []


async def test_a_thread_with_no_prediction_or_outside_the_share_is_refused(owner_client, sessions, owner):
    agent, thread = await worker_with_a_prediction(owner_client, sessions, owner, percent=5)
    missing = await owner_client.post(feedback_url(agent["id"]), json={"thread_id": "th999999", **GOOD})
    assert missing.status_code == 404 and code_of(missing) == "no_prediction"
    outside = next(f"th{n}" for n in range(100_000) if not gmail_share.in_share(rule(5), f"th{n}"))
    async with sessions() as db:
        db.add(ShadowPrediction(owner_id=owner, agent_id=agent["id"], account_id=ACCOUNT, thread_id=outside, rank=2, category="bug",
                                turn_id=2, predicted_at=datetime.now(UTC) - timedelta(days=1)))
        await db.commit()
    response = await owner_client.post(feedback_url(agent["id"]), json={"thread_id": outside, **GOOD})
    assert response.status_code == 403 and code_of(response) == "outside_share"


async def test_the_counts_have_the_exact_shape_and_only_counts(owner_client, sessions, owner):
    agent, thread = await worker_with_a_prediction(owner_client, sessions, owner)
    await owner_client.post(feedback_url(agent["id"]), json={"thread_id": thread, **GOOD, "reason": "wrong_policy", "note": "private words"})
    body = (await owner_client.get(feedback_url(agent["id"]))).json()
    assert set(body) == {"agent_id", "window_days", "total", "by_reason", "by_kind"}
    assert body["window_days"] == 28 and body["total"] == 1 and body["by_reason"]["wrong_policy"] == 1 and body["by_kind"] == {"draft": 1, "ranking": 0}
    assert "private words" not in str(body) and thread not in str(body)


@pytest.mark.parametrize("query", ["?days=0", "?days=91", "?days=abc"])
async def test_bad_query_values_are_422(owner_client, query):
    agent = await made(owner_client)
    assert (await owner_client.get(feedback_url(agent["id"], query))).status_code == 422


async def test_the_suggestion_has_the_exact_shape(owner_client, admin_client):
    agent = await made(owner_client)
    for client in (owner_client, admin_client):
        response = await client.get(suggestion_url(agent["id"]))
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == SUGGESTION_SHAPE and set(body["evidence"]) == EVIDENCE_SHAPE
        assert body["state"] == "none" and body["suggested_percent"] is None and body["current_percent"] == 100 and body["reason"]


async def test_the_suggestion_only_reads_and_drops_extra_fields(owner_client, monkeypatch):
    agent = await made(owner_client)
    seen = []

    async def fake_build(rt, agent_id, *, now=None, refresh=False):
        seen.append((agent_id, refresh))
        return {"agent_id": agent_id, "state": "raise", "account_id": "ca_a", "current_percent": 10, "suggested_percent": 25,
                "evidence": {"evaluated": 20, "usable": 17, "usable_rate": 0.85, "lower": 0.64, "upper": 0.95, "days": 20, "wrong_policy": 0,
                             "draft_text": "must be dropped"}, "reason": "r", "seed": "must be dropped"}

    monkeypatch.setattr(share_suggestion, "build", fake_build)
    body = (await owner_client.get(suggestion_url(agent["id"], "?refresh=true"))).json()
    assert seen == [(agent["id"], True)] and body["state"] == "raise" and body["suggested_percent"] == 25
    assert "seed" not in body and "draft_text" not in body["evidence"]


async def test_another_owners_agent_is_a_404_everywhere(owner_client, sessions):
    async with sessions() as db:
        theirs = await agent_store.create_agent(db, owner_id="someone-else-" + uuid4().hex, name="X")
    try:
        assert (await owner_client.get(suggestion_url(theirs.id))).status_code == 404
        assert (await owner_client.get(feedback_url(theirs.id))).status_code == 404
        response = await owner_client.post(feedback_url(theirs.id), json={"thread_id": "t", **GOOD})
        assert response.status_code == 404 and code_of(response) == "agent_not_found"
        assert (await owner_client.get(suggestion_url(987654321))).status_code == 404
    finally:
        async with sessions() as db:
            await db.execute(delete(Agent).where(Agent.id == theirs.id))
            await db.commit()


async def test_a_phone_without_admin_access_is_refused(owner_client, phone_client):
    agent = await made(owner_client)
    for response, code in (
        (await phone_client.get(suggestion_url(agent["id"])), "share_suggestion_requires_admin"),
        (await phone_client.get(feedback_url(agent["id"])), "shadow_feedback_requires_admin"),
        (await phone_client.post(feedback_url(agent["id"]), json={"thread_id": "t", **GOOD}), "shadow_feedback_requires_admin"),
    ):
        assert response.status_code == 403 and code_of(response) == code


def test_the_routes_need_the_admin_scope_before_the_handler_even_runs():
    from app.auth.policy import required_scope

    assert required_scope("GET", "/agents/12/share-suggestion") == "admin"
    assert required_scope("GET", "/agents/12/shadow-feedback") == "admin"
    assert required_scope("POST", "/agents/12/shadow-feedback") == "admin"
