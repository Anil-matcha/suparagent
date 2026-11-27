"""`POST /templates/hire`: workers added together from template ids, or not at all."""

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.models import Agent, Routine, RoutineRun
from app.routers.agents import get_runtime as agents_runtime
from app.routers.templates import router as templates_router
from app.services import agent_skills
from app.templates import catalog
from tests.services.thread_harness import cleanup, cli, owner, rt, sessions  # noqa: F401

SEO = "marketing-seo"
LEADS = "sales-sales"
HIRE = "/api/v1/templates/hire"


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


@pytest.fixture(autouse=True)
async def hire_cleanup(sessions, owner):
    yield
    async with sessions() as db:
        ids = select(Routine.id).where(Routine.owner_id == owner)
        await db.execute(delete(RoutineRun).where(RoutineRun.routine_id.in_(ids)))
        await db.execute(delete(Routine).where(Routine.owner_id == owner))
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


@pytest.fixture
async def api(rt, cli):  # `cli` keeps every agent folder under tmp_path
    app = FastAPI()
    app.include_router(templates_router, prefix="/api/v1")
    app.dependency_overrides[agents_runtime] = lambda: rt
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app, client=("127.0.0.1", 50000)), base_url="http://localhost"
    ) as client:
        yield client


async def agents_of(sessions, owner):
    async with sessions() as db:
        return list((await db.execute(select(Agent).where(Agent.owner_id == owner).order_by(Agent.id))).scalars())


async def routines_of(sessions, owner):
    async with sessions() as db:
        return list((await db.execute(select(Routine).where(Routine.owner_id == owner))).scalars())


async def test_two_workers_are_added_together(api, sessions, owner):
    response = await api.post(HIRE, json={"template_ids": [SEO, LEADS]})
    assert response.status_code == 201, response.text
    workers = response.json()["workers"]
    assert [w["template_id"] for w in workers] == [SEO, LEADS]
    seo = catalog.get(SEO)
    assert [w["name"] for w in workers] == [seo.name, catalog.get(LEADS).name]
    assert workers[0]["skills"] == [s.name for s in seo.skills]
    assert [r["template_routine_id"] for r in workers[0]["routines"]] == [r.id for r in seo.routines]
    agents = await agents_of(sessions, owner)
    assert len(agents) == 2
    # The text comes from the catalog, not from the request.
    assert agents[0].instructions == seo.full_instructions
    assert agents[0].title == seo.role


async def test_workers_ask_first_and_hold_no_grants(api, sessions, owner):
    assert (await api.post(HIRE, json={"template_ids": [SEO]})).status_code == 201
    (agent,) = await agents_of(sessions, owner)
    assert agent.approval_mode == "ask"
    assert not agent.connector_scopes
    assert agent.team_id is None


async def test_starter_routines_are_paused_and_unscheduled(api, sessions, owner):
    response = await api.post(HIRE, json={"template_ids": [SEO]})
    assert all(r["paused"] for r in response.json()["workers"][0]["routines"])
    rows = await routines_of(sessions, owner)
    assert len(rows) == len(catalog.get(SEO).routines)
    assert all(r.paused and r.next_run_at is None and r.schedule_kind == "manual" for r in rows)


async def test_skills_are_installed_for_each_worker(api, sessions, owner):
    response = await api.post(HIRE, json={"template_ids": [SEO]})
    agent_id = response.json()["workers"][0]["agent_id"]
    saved = {s.name: s for s in agent_skills.list_skills(agent_id)}
    assert set(saved) == {s.name for s in catalog.get(SEO).skills}
    assert all(s.source == f"template:{SEO}" for s in saved.values())


async def test_hiring_again_gives_unique_names(api, sessions, owner):
    for _ in range(2):
        assert (await api.post(HIRE, json={"template_ids": [SEO]})).status_code == 201
    names = [a.name for a in await agents_of(sessions, owner)]
    assert names == [catalog.get(SEO).name, f"{catalog.get(SEO).name} 2"]


async def test_a_failure_leaves_nothing_behind_and_names_the_worker(api, sessions, owner, monkeypatch):
    real = agent_skills.write_skill
    written: list[int] = []

    def flaky(agent_id, name, *args, **kwargs):
        if len(written) >= len(catalog.get(SEO).skills) and name == catalog.get(LEADS).skills[0].name:
            raise agent_skills.SkillError(500, "skill_write_failed", "disk full")
        written.append(agent_id)
        return real(agent_id, name, *args, **kwargs)

    monkeypatch.setattr(agent_skills, "write_skill", flaky)
    response = await api.post(HIRE, json={"template_ids": [SEO, LEADS]})
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["worker"] == LEADS and detail["error_code"] == "skill_write_failed"
    assert await agents_of(sessions, owner) == []
    assert await routines_of(sessions, owner) == []
    # The first worker's skill files were removed again.
    assert all(agent_skills.list_skills(agent_id) == [] for agent_id in set(written))


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"template_ids": ["no-such-worker"]}, 404, "unknown_template"),
        ({"template_ids": [SEO, SEO]}, 400, "duplicate_worker"),
        ({"template_ids": [SEO], "provider": "no-such-engine"}, 422, "unknown_provider"),
    ],
)
async def test_bad_requests_create_nothing(api, sessions, owner, body, status, code):
    response = await api.post(HIRE, json=body)
    assert response.status_code == status
    assert response.json()["detail"]["error_code"] == code
    assert await agents_of(sessions, owner) == []


async def test_an_empty_list_is_refused(api):
    assert (await api.post(HIRE, json={"template_ids": []})).status_code == 422


async def test_a_worker_that_cannot_be_connected_is_refused(api, sessions, owner):
    blocked = next((t for t in catalog.TEMPLATES if not t.is_available(frozenset())), None)
    if blocked is None:
        pytest.skip("every bundled worker is addable today")
    response = await api.post(HIRE, json={"template_ids": [blocked.id]})
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "template_unavailable"
    assert await agents_of(sessions, owner) == []


async def test_a_muapi_worker_can_be_hired_before_muapi_is_connected(api, sessions, owner):
    assert "muapi" in catalog.get(SEO).requires
    assert (await api.post(HIRE, json={"template_ids": [SEO]})).status_code == 201


UNDO = "/api/v1/templates/hire/undo"


async def _hire_two(api):
    response = await api.post(HIRE, json={"template_ids": [SEO, LEADS]})
    assert response.status_code == 201, response.text
    return [w["agent_id"] for w in response.json()["workers"]]


async def test_undo_removes_the_hired_workers_and_their_routines(api, sessions, owner):
    ids = await _hire_two(api)
    response = await api.post(UNDO, json={"agent_ids": ids})
    assert response.status_code == 200, response.text
    assert response.json() == {"removed": ids}
    assert await agents_of(sessions, owner) == []
    assert await routines_of(sessions, owner) == []
    assert agent_skills.list_skills(ids[0]) == []


async def test_undo_removes_nothing_if_one_worker_was_edited(api, sessions, owner):
    ids = await _hire_two(api)
    async with sessions() as db:
        agent = await db.get(Agent, ids[1])
        agent.instructions = agent.instructions + " Edited."
        agent.updated_at = agent.created_at.replace(year=agent.created_at.year + 1)
        await db.commit()
    response = await api.post(UNDO, json={"agent_ids": ids})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error_code"] == "worker_changed"
    assert detail["worker"] == str(ids[1])
    assert len(await agents_of(sessions, owner)) == 2
    assert len(await routines_of(sessions, owner)) > 0


async def test_undo_refuses_when_a_routine_was_switched_on(api, sessions, owner):
    ids = await _hire_two(api)
    async with sessions() as db:
        routine = (await db.execute(select(Routine).where(Routine.agent_id == ids[0]))).scalars().first()
        routine.paused = False
        await db.commit()
    response = await api.post(UNDO, json={"agent_ids": ids})
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "worker_in_use"
    assert len(await agents_of(sessions, owner)) == 2


async def test_undo_of_an_unknown_worker_is_refused(api):
    response = await api.post(UNDO, json={"agent_ids": [999999]})
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "worker_missing"


def test_routine_names_keep_acronyms_in_capitals():
    from app.templates import hire

    routine = type("R", (), {"id": "seo-keyword-brief"})()
    assert hire._routine_name(routine) == "SEO keyword brief"
    assert hire._routine_name(type("R", (), {"id": "daily-brief"})()) == "Daily brief"
