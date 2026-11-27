"""GET and POST /api/v1/agents/{id}/worker-labels: owner-scoped, admin or owner only, exact shape. What an undo sends to
Gmail is tested in tests/services/test_worker_labels_undo.py."""
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import delete

from app.db.models import Agent
from app.routers.agents import get_runtime as agents_runtime
from app.routers.agents import router as agents_router
from app.routers.worker_labels import router as worker_labels_router
from app.services import agent_store, worker_labels
from tests.routers.test_agents_api import agent_cleanup, client_for, code_of, local_enabled  # noqa: F401
from tests.routers.test_agents_manage_api import ADMIN, PHONE, AsPrincipal, made
from tests.services.thread_harness import cleanup, cli, owner, rt, sessions  # noqa: F401

SUMMARY = {"agent_id", "applied", "threads", "labels", "last_applied_at", "undoable", "accounts"}


def app_as(rt, principal=None) -> FastAPI:
    app = FastAPI()
    app.include_router(agents_router, prefix="/api/v1")
    app.include_router(worker_labels_router, prefix="/api/v1")
    app.dependency_overrides[agents_runtime] = lambda: rt
    app.add_middleware(AsPrincipal, principal=principal)
    return app


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


def url(agent_id, tail=""):
    return f"/api/v1/agents/{agent_id}/worker-labels{tail}"


async def test_the_owner_and_an_admin_get_the_exact_shape(owner_client, admin_client):
    agent = await made(owner_client)
    for client in (owner_client, admin_client):
        response = await client.get(url(agent["id"]))
        assert response.status_code == 200, response.text
        assert response.json() == {"agent_id": agent["id"], "applied": 0, "threads": 0, "labels": [], "last_applied_at": None,
                                   "undoable": 0, "accounts": []}
        assert set(response.json()) == SUMMARY


async def test_undo_answers_counts_only_and_is_safe_to_repeat(owner_client, admin_client):
    agent = await made(owner_client)
    for client in (owner_client, admin_client):
        response = await client.post(url(agent["id"], "/undo"))
        assert response.status_code == 200, response.text
        assert response.json() == {"removed": 0, "already_removed": 0, "failed": 0}


async def test_the_summary_and_undo_reach_the_service_and_drop_extra_fields(owner_client, monkeypatch):
    agent = await made(owner_client)
    seen = []

    async def fake_summary(rt, agent_id):
        seen.append(("summary", agent_id))
        return {"agent_id": agent_id, "applied": 3, "threads": 2, "labels": [{"name": "Worker/Bug", "count": 3, "label_id": "must be dropped"}],
                "last_applied_at": datetime(2026, 11, 26).isoformat(), "undoable": 3, "accounts": ["ca_a"], "secret_extra": "must be dropped"}

    async def fake_undo(rt, agent_id):
        seen.append(("undo", agent_id))
        return {"removed": 2, "already_removed": 1, "failed": 0, "thread_ids": ["must be dropped"]}

    monkeypatch.setattr(worker_labels, "summary", fake_summary)
    monkeypatch.setattr(worker_labels, "undo", fake_undo)
    body = (await owner_client.get(url(agent["id"]))).json()
    assert body["labels"] == [{"name": "Worker/Bug", "count": 3}] and "secret_extra" not in body  # the response model is the whole shape
    assert (await owner_client.post(url(agent["id"], "/undo"))).json() == {"removed": 2, "already_removed": 1, "failed": 0}
    assert seen == [("summary", agent["id"]), ("undo", agent["id"])]


async def test_another_owners_agent_is_a_404(owner_client, sessions):
    async with sessions() as db:
        theirs = await agent_store.create_agent(db, owner_id="someone-else-" + uuid4().hex, name="X")
    try:
        for call in (owner_client.get(url(theirs.id)), owner_client.post(url(theirs.id, "/undo")),
                     owner_client.get(url(987654321)), owner_client.post(url(987654321, "/undo"))):
            assert (await call).status_code == 404
    finally:
        async with sessions() as db:
            await db.execute(delete(Agent).where(Agent.id == theirs.id))
            await db.commit()


async def test_a_phone_without_admin_access_is_refused_for_both(owner_client, phone_client):
    agent = await made(owner_client)
    for response in (await phone_client.get(url(agent["id"])), await phone_client.post(url(agent["id"], "/undo"))):
        assert response.status_code == 403 and code_of(response) == "worker_labels_require_admin"


async def test_an_archived_agent_is_a_404(owner_client, sessions):
    agent = await made(owner_client)
    assert (await owner_client.patch(f"/api/v1/agents/{agent['id']}", json={"archived": True})).status_code == 200
    assert (await owner_client.get(url(agent["id"]))).status_code == 404


def test_both_routes_need_the_admin_scope_before_the_handler_even_runs():
    from app.auth.policy import required_scope

    assert required_scope("GET", "/agents/12/worker-labels") == "admin"
    assert required_scope("POST", "/agents/12/worker-labels/undo") == "admin"
