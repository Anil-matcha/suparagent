"""Taking an agent's Gmail access away through the agents route stops the new-mail wake of its routines (BL-177)."""
from sqlalchemy import delete, select

from app.db.models import Agent, AppEventTrigger, Routine
from app.services import routine_service
from tests.routers.test_engines_api import api, local_enabled  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401


async def test_lowering_the_agents_access_marks_its_wake_in_error_and_keeps_the_routine(api, sessions, owner):  # noqa: F811
    created = await api.post("/api/v1/agents", json={"name": "Mail reader", "provider": "claude"})
    assert created.status_code == 201, created.text
    agent_id = created.json()["id"]
    assert (await api.patch(f"/api/v1/agents/{agent_id}", json={"connector_scopes": {"gmail": "read"}})).status_code == 200
    async with sessions() as db:  # the account list is set directly: the route would check it against a live Composio
        (await db.get(Agent, agent_id)).connector_account_scopes = {"gmail": ["ca_g"]}
        await db.commit()
    try:
        async with sessions() as db:
            routine = await routine_service.create_routine(db, owner, {"agent_id": agent_id, "name": "Mail", "prompt": "p",
                                                                       "schedule_kind": "manual", "schedule_spec": {}, "timezone": "UTC"})
            routine.connected_app_grants = [{"tool": "gmail_fetch_emails", "account_id": "ca_g"}]
            routine.app_event = {"app": "gmail", "account_id": "ca_g", "query": "", "debounce_minutes": 5, "max_runs_per_day": 12}
            db.add(AppEventTrigger(owner_id=owner, routine_id=routine.id, composio_trigger_id="ti_1", composio_account_id="ca_g",
                                   slug="GMAIL_NEW_GMAIL_MESSAGE", state="active"))
            await db.commit()
            routine_id = routine.id
        # Changing something unrelated leaves the wake alone.
        assert (await api.patch(f"/api/v1/agents/{agent_id}", json={"title": "Reader"})).status_code == 200
        async with sessions() as db:
            assert (await db.scalar(select(AppEventTrigger.state))) == "active"
        # Taking the access away stops it; the routine stays.
        assert (await api.patch(f"/api/v1/agents/{agent_id}", json={"connector_scopes": {}})).status_code == 200
        async with sessions() as db:
            row = (await db.execute(select(AppEventTrigger))).scalar_one()
            assert row.state == "error" and "not allowed to read" in row.last_error
            assert await db.get(Routine, routine_id) is not None
    finally:
        async with sessions() as db:
            await db.execute(delete(Routine).where(Routine.owner_id == owner))
            await db.commit()
