"""The owner's side of learning from past replies (BL-174): the opt-in, the lessons to review, forget. Owner-scoped,
admin or owner only, exact shapes. What the worker's tools do is in tests/services/test_history_reader.py."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import delete

from app.db.models import Agent
from app.routers.agents import get_runtime as agents_runtime
from app.routers.agents import router as agents_router
from app.routers.history_learning import router as history_router
from app.services import agent_store, history_learning as hl, memory_proposals, memory_topics
from tests.routers.test_agents_api import agent_cleanup, client_for, code_of, local_enabled  # noqa: F401
from tests.routers.test_agents_manage_api import ADMIN, PHONE, AsPrincipal, made
from tests.services.thread_harness import cleanup, cli, owner, rt, sessions  # noqa: F401

ACCOUNT_FIELDS = {"account_id", "enabled", "declined", "query", "max_threads", "months", "allowed", "threads_read", "stage_done",
                  "finished", "started_at", "last_read_at"}


def app_as(rt, principal=None) -> FastAPI:
    app = FastAPI()
    app.include_router(agents_router, prefix="/api/v1")
    app.include_router(history_router, prefix="/api/v1")
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
    return f"/api/v1/agents/{agent_id}/{tail}".rstrip("/")


async def with_gmail(sessions, agent, accounts=("ca_g",), level="read"):
    async with sessions() as db:
        await agent_store.update_agent(db, agent["id"], connector_scopes={"gmail": level}, connector_account_scopes={"gmail": list(accounts)})


def day(n):
    return (datetime.now(UTC) - timedelta(days=n)).date().isoformat()


def lesson(agent_id, **more):
    body = {"lesson_type": "template", "pattern": "Where is my order", "text": "It ships in two days.", "count": 4,
            "newest": day(3), "oldest": day(30)}
    body.update(more)
    return hl.propose_lesson(agent_id, **body)


# -- the opt-in -----------------------------------------------------------------------------------------------------------------

async def test_an_agent_without_gmail_has_no_accounts_and_cannot_be_switched_on(owner_client, admin_client):
    agent = await made(owner_client)
    for client in (owner_client, admin_client):
        body = (await client.get(url(agent["id"], "history-learning"))).json()
        assert body == {"agent_id": agent["id"], "accounts": [], "pending": 0, "accepted": 0}
    refused = await owner_client.put(url(agent["id"], "history-learning/ca_g"), json={"enabled": True, "query": "label:support"})
    assert refused.status_code == 422 and code_of(refused) == "agent_cannot_read_mail"


async def test_turning_it_on_needs_a_search_then_shows_one_stage_and_extends_only_on_request(owner_client, sessions):
    agent = await made(owner_client)
    await with_gmail(sessions, agent)
    listed = (await owner_client.get(url(agent["id"], "history-learning"))).json()
    assert [a["account_id"] for a in listed["accounts"]] == ["ca_g"] and listed["accounts"][0]["enabled"] is False
    no_query = await owner_client.put(url(agent["id"], "history-learning/ca_g"), json={"enabled": True})
    assert no_query.status_code == 422 and code_of(no_query) == "query_required"
    on = await owner_client.put(url(agent["id"], "history-learning/ca_g"), json={"enabled": True, "query": "label:support"})
    assert on.status_code == 200 and set(on.json()) == ACCOUNT_FIELDS
    assert (on.json()["enabled"], on.json()["allowed"], on.json()["max_threads"], on.json()["months"]) == (True, 30, 100, 6)
    more = await owner_client.put(url(agent["id"], "history-learning/ca_g"), json={"extend": 30})
    assert more.json()["allowed"] == 60


async def test_not_now_needs_no_gmail_access_and_is_remembered(owner_client, sessions):
    agent = await made(owner_client)
    await with_gmail(sessions, agent)
    response = await owner_client.put(url(agent["id"], "history-learning/ca_g"), json={"declined": True})
    assert response.status_code == 200 and response.json()["declined"] is True
    assert (await owner_client.get(url(agent["id"], "history-learning"))).json()["accounts"][0]["declined"] is True


@pytest.mark.parametrize("body", [{"max_threads": 5}, {"max_threads": 500}, {"months": 9}, {"surprise": 1}, {"enabled": "yes"}])
async def test_bad_values_and_unknown_fields_are_422(owner_client, sessions, body):
    agent = await made(owner_client)
    await with_gmail(sessions, agent)
    assert (await owner_client.put(url(agent["id"], "history-learning/ca_g"), json=body)).status_code == 422


async def test_an_account_the_agent_does_not_have_is_refused_for_turning_on(owner_client, sessions):
    agent = await made(owner_client)
    await with_gmail(sessions, agent, accounts=("ca_g",))
    refused = await owner_client.put(url(agent["id"], "history-learning/ca_other"), json={"enabled": True, "query": "label:support"})
    assert refused.status_code == 422 and code_of(refused) == "agent_cannot_read_mail"


# -- the lessons ----------------------------------------------------------------------------------------------------------------

async def test_lessons_are_listed_accepted_into_the_answer_library_and_dismissed(owner_client, sessions):
    agent = await made(owner_client)
    await with_gmail(sessions, agent)
    await owner_client.put(url(agent["id"], "history-learning/ca_g"), json={"enabled": True, "query": "label:support"})
    keep, drop = lesson(agent["id"]), lesson(agent["id"], pattern="Cancel my plan")
    listed = (await owner_client.get(url(agent["id"], "lessons"))).json()
    assert {l["id"] for l in listed["pending"]} == {keep["id"], drop["id"]} and listed["accepted"] == []
    assert set(listed["pending"][0]) >= {"id", "type", "pattern", "text", "count", "newest", "oldest", "status"}
    assert "identity" not in listed["pending"][0]
    accepted = await owner_client.post(url(agent["id"], f"lessons/{keep['id']}/accept"), json={"text": "Call 415 555 0199, it ships soon."})
    assert accepted.status_code == 200 and accepted.json()["destination"] == "answer-library" and accepted.json()["entry_id"]
    note = memory_topics.read_topic(agent["id"], hl.NOTE).text
    assert hl.HISTORY_MARK in note and "it ships soon" in note and "555 0199" not in note
    assert (await owner_client.post(url(agent["id"], f"lessons/{drop['id']}/dismiss"))).status_code == 204
    after = (await owner_client.get(url(agent["id"], "lessons"))).json()
    assert after["pending"] == [] and [l["id"] for l in after["accepted"]] == [keep["id"]]
    assert (await owner_client.post(url(agent["id"], f"lessons/{keep['id']}/accept"))).status_code == 404
    assert (await owner_client.post(url(agent["id"], "lessons/nope/dismiss"))).status_code == 404


async def test_a_policy_lesson_becomes_a_suggestion_and_never_a_note(owner_client, sessions):
    agent = await made(owner_client)
    await with_gmail(sessions, agent)
    await owner_client.put(url(agent["id"], "history-learning/ca_g"), json={"enabled": True, "query": "label:support"})
    policy = lesson(agent["id"], lesson_type="policy", pattern="Refunds", text="Refund offered in 6 of 8 requests.")
    accepted = await owner_client.post(url(agent["id"], f"lessons/{policy['id']}/accept"))
    assert accepted.status_code == 200 and accepted.json()["destination"] == "suggestions"
    assert not memory_topics.read_topic(agent["id"], hl.NOTE).exists
    assert len(memory_proposals.pending(agent["id"])) == 1


async def test_forget_removes_what_was_learned(owner_client, sessions):
    agent = await made(owner_client)
    await with_gmail(sessions, agent)
    await owner_client.put(url(agent["id"], "history-learning/ca_g"), json={"enabled": True, "query": "label:support"})
    first = lesson(agent["id"])
    await owner_client.post(url(agent["id"], f"lessons/{first['id']}/accept"))
    lesson(agent["id"], pattern="Waiting one")
    response = await owner_client.delete(url(agent["id"], "history-learning"))
    assert response.status_code == 200 and response.json() == {"entries_removed": 1, "lessons_removed": 2}
    assert hl.HISTORY_MARK not in memory_topics.read_topic(agent["id"], hl.NOTE).text
    assert (await owner_client.get(url(agent["id"], "history-learning"))).json()["accounts"][0]["enabled"] is False


# -- who may use it ---------------------------------------------------------------------------------------------------------------

async def test_a_phone_without_admin_access_is_refused_everywhere(owner_client, phone_client, sessions):
    agent = await made(owner_client)
    await with_gmail(sessions, agent)
    calls = [phone_client.get(url(agent["id"], "history-learning")), phone_client.get(url(agent["id"], "lessons")),
             phone_client.put(url(agent["id"], "history-learning/ca_g"), json={"declined": True}),
             phone_client.delete(url(agent["id"], "history-learning")), phone_client.post(url(agent["id"], "lessons/x/accept")),
             phone_client.post(url(agent["id"], "lessons/x/dismiss"))]
    for call in calls:
        response = await call
        assert response.status_code == 403 and code_of(response) == "history_learning_requires_admin"


async def test_another_owners_or_an_archived_agent_is_a_404(owner_client, sessions):
    async with sessions() as db:
        theirs = await agent_store.create_agent(db, owner_id="someone-else-" + uuid4().hex, name="X")
    try:
        for target in (theirs.id, 987654321):
            for call in (owner_client.get(url(target, "history-learning")), owner_client.get(url(target, "lessons")),
                         owner_client.delete(url(target, "history-learning")),
                         owner_client.put(url(target, "history-learning/ca_g"), json={"declined": True})):
                assert (await call).status_code == 404
    finally:
        async with sessions() as db:
            await db.execute(delete(Agent).where(Agent.id == theirs.id))
            await db.commit()
    mine = await made(owner_client)
    assert (await owner_client.patch(f"/api/v1/agents/{mine['id']}", json={"archived": True})).status_code == 200
    assert (await owner_client.get(url(mine["id"], "history-learning"))).status_code == 404


def test_every_route_needs_the_admin_scope_before_the_handler_runs():
    from app.auth.policy import required_scope

    for method, path in [("GET", "/agents/12/history-learning"), ("PUT", "/agents/12/history-learning/ca_g"),
                         ("DELETE", "/agents/12/history-learning"), ("GET", "/agents/12/lessons"),
                         ("POST", "/agents/12/lessons/abc/accept"), ("POST", "/agents/12/lessons/abc/dismiss")]:
        assert required_scope(method, path) == "admin", (method, path)
