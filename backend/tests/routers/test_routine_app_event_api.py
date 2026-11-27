"""A routine's new-mail trigger over HTTP (BL-177): real routes, scheduler and database, a fake Composio."""
import httpx
import pytest
from sqlalchemy import delete, select

from app.auth.policy import required_scope
from app.core.config import settings
from app.db.models import Agent, AppEventTrigger
from app.db.models.user_credential import UserCredential
from app.services import composio_bridge, composio_service
from app.services.composio_client import ComposioClient
from tests.routers.test_routines_api import (  # noqa: F401
    api, agent, scheduler, local_enabled, agent_args, routine_cleanup, make, app_for, client_for,
    sessions, owner, rt, cleanup, cli,
)
from tests.services.composio_trigger_fake import TriggerFake

KEY = "ak_ROUTERKEY_abcdef123456"
ACCOUNT = "ca_g"
GRANTS = [{"tool": "gmail_fetch_emails", "account_id": ACCOUNT}]
EVENT = {"account_id": ACCOUNT, "query": "label:support"}


@pytest.fixture
async def mail(agent, sessions, owner, monkeypatch):
    """A Composio project with a Gmail account the agent may read, and a public address for events."""
    fake = TriggerFake()
    monkeypatch.setattr(composio_bridge, "make_client", lambda key: ComposioClient(key, transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setattr(settings, "SUPARAGENT_PUBLIC_URL", "https://dots.example.com")
    async with sessions() as db:
        stored = await db.get(Agent, agent.id)
        stored.connector_scopes, stored.connector_account_scopes = {"gmail": "read"}, {"gmail": [ACCOUNT]}
        await composio_service._save(db, owner, {"api_key": KEY, "user_id": "u1", "session_id": "sess_1"})
        await db.commit()
    yield fake
    async with sessions() as db:
        await db.execute(delete(AppEventTrigger).where(AppEventTrigger.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


def path(routine):
    return f"/api/v1/routines/{routine['id']}"


def code_of(response):
    return response.json()["detail"]["error_code"]


async def made(api, agent, **extra):
    return await make(api, agent, connected_app_grants=GRANTS, app_event=EVENT, **extra)


async def test_creating_a_routine_with_a_new_mail_wake_creates_one_webhook_and_one_instance(api, agent, mail):
    created = await made(api, agent)
    assert created["app_event"]["account_id"] == ACCOUNT and created["app_event"]["query"] == "label:support"
    assert created["app_event"]["debounce_minutes"] == 5 and created["app_event"]["max_runs_per_day"] == 12
    status = created["app_event_status"]
    assert status["state"] == "active" and status["can_receive"] is True and status["today"]["received"] == 0
    assert len(mail.triggers) == 1 and len(mail.subs) == 1
    (sub,), (trigger_id,) = mail.subs.values(), mail.triggers
    text = (await api.get(path(created))).text + (await api.get("/api/v1/routines")).text
    assert sub["secret"] not in text and trigger_id not in text and sub["id"] not in text
    assert sub["webhook_url"].rsplit("/", 1)[1] not in text            # nor the random route id of the webhook address


async def test_a_wake_that_cannot_be_set_up_does_not_leave_a_half_made_routine(api, agent, mail, monkeypatch):
    monkeypatch.setattr(settings, "SUPARAGENT_PUBLIC_URL", "")
    response = await api.post("/api/v1/routines", json={"agent_id": agent.id, "name": "Mail", "prompt": "p", "connected_app_grants": GRANTS, "app_event": EVENT})
    assert response.status_code == 409 and code_of(response) == "no_public_endpoint"
    assert "relay" in response.json()["detail"]["message"]
    assert (await api.get("/api/v1/routines")).json() == [] and mail.triggers == {} and mail.subs == {}


async def test_a_routine_that_cannot_read_that_mail_is_refused_before_anything_is_sent(api, agent, mail):
    response = await api.post("/api/v1/routines", json={"agent_id": agent.id, "name": "Mail", "prompt": "p", "app_event": EVENT})
    assert response.status_code == 422 and code_of(response) == "routine_cannot_read_mail"
    assert (await api.get("/api/v1/routines")).json() == [] and not [c for c in mail.calls if c[0] != "GET"]


async def test_bad_settings_are_rejected_by_the_schema(api, agent, mail):
    for bad in ({"account_id": ACCOUNT, "debounce_minutes": 0}, {"account_id": ACCOUNT, "max_runs_per_day": 99}, {"account_id": ACCOUNT, "extra": 1},
                {"account_id": ACCOUNT, "query": "x" * 201}, {"account_id": ACCOUNT, "quiet": {"from": "20:00", "to": "20:00"}}, {"account_id": "a b"}):
        response = await api.post("/api/v1/routines", json={"agent_id": agent.id, "name": "Mail", "prompt": "p", "connected_app_grants": GRANTS, "app_event": bad})
        assert response.status_code == 422, bad
    assert mail.calls == [] or not [c for c in mail.calls if c[0] != "GET"]


async def test_quiet_hours_and_limits_are_kept(api, agent, mail):
    event = {**EVENT, "debounce_minutes": 10, "max_runs_per_day": 3, "quiet": {"from": "20:00", "to": "07:00"}}
    created = await make(api, agent, connected_app_grants=GRANTS, app_event=event)
    assert created["app_event"]["quiet"] == {"from": "20:00", "to": "07:00"} and created["app_event"]["debounce_minutes"] == 10


async def test_changing_the_search_replaces_the_instance_and_null_switches_the_wake_off(api, agent, mail):
    created = await made(api, agent)
    (first,) = mail.triggers
    changed = await api.patch(path(created), json={"app_event": {**EVENT, "query": "label:vip"}})
    assert changed.status_code == 200 and changed.json()["app_event"]["query"] == "label:vip"
    assert list(mail.triggers) != [first] and len(mail.triggers) == 1
    off = await api.patch(path(created), json={"app_event": None})
    assert off.status_code == 200 and off.json()["app_event"] is None and off.json()["app_event_status"] is None
    assert mail.triggers == {} and mail.subs == {}


async def test_an_unrelated_edit_leaves_the_wake_alone(api, agent, mail):
    created = await made(api, agent)
    calls = len(mail.calls)
    renamed = await api.patch(path(created), json={"name": "Renamed"})
    assert renamed.status_code == 200 and renamed.json()["app_event"]["query"] == "label:support" and len(mail.triggers) == 1
    assert len(mail.calls) == calls                              # no remote call for an edit that does not touch the wake


async def test_a_failed_wake_change_still_saves_the_other_edits_and_says_why(api, agent, mail, monkeypatch):
    created = await made(api, agent)
    monkeypatch.setattr(settings, "SUPARAGENT_PUBLIC_URL", "")
    response = await api.patch(path(created), json={"name": "Renamed", "app_event": {**EVENT, "query": "label:vip"}})
    assert response.status_code == 409 and code_of(response) == "no_public_endpoint"
    assert (await api.get(path(created))).json()["name"] == "Renamed" and len(mail.triggers) == 1


async def test_pausing_and_resuming_the_routine_pauses_and_resumes_the_remote_instance(api, agent, mail):
    created = await made(api, agent)
    (trigger,) = mail.triggers.values()
    paused = (await api.post(path(created) + "/pause")).json()
    assert paused["paused"] is True and paused["app_event_status"]["state"] == "paused" and trigger["disabled_at"]
    resumed = (await api.post(path(created) + "/resume")).json()
    assert resumed["app_event_status"]["state"] == "active" and not trigger["disabled_at"]


async def test_pausing_works_when_composio_is_down_and_says_so_in_the_status(api, agent, mail):
    created = await made(api, agent)
    mail.fail["PATCH /trigger_instances/manage/"] = 500
    paused = await api.post(path(created) + "/pause")
    assert paused.status_code == 200 and paused.json()["paused"] is True
    assert paused.json()["app_event_status"]["state"] == "active" and paused.json()["app_event_status"]["last_error"]


async def test_deleting_the_routine_deletes_the_remote_instance_and_our_webhook(api, agent, mail):
    created = await made(api, agent)
    assert (await api.delete(path(created))).status_code == 204
    assert mail.triggers == {} and mail.subs == {} and (await api.get(path(created))).status_code == 404


async def test_deleting_the_routine_is_not_blocked_by_composio_and_the_instance_is_remembered(api, agent, mail, sessions, owner):
    created = await made(api, agent)
    (trigger_id,) = mail.triggers
    mail.fail["DELETE /trigger_instances/manage/"] = 500
    assert (await api.delete(path(created))).status_code == 204
    async with sessions() as db:
        assert (await composio_service.load(db, owner))["pending_trigger_deletes"] == [trigger_id]


async def test_losing_the_gmail_grant_marks_the_wake_in_error_and_keeps_the_routine(api, agent, mail):
    created = await made(api, agent)
    changed = await api.patch(path(created), json={"connected_app_grants": []})
    assert changed.status_code == 200 and changed.json()["app_event_status"]["state"] == "error"
    assert "permission to read" in changed.json()["app_event_status"]["last_error"] and changed.json()["paused"] is False


async def test_the_list_carries_the_status_only_for_routines_that_have_a_wake(api, agent, mail):
    plain = await make(api, agent, name="Plain")
    wake = await made(api, agent, name="Wake")
    by_id = {r["id"]: r for r in (await api.get("/api/v1/routines")).json()}
    assert by_id[plain["id"]]["app_event"] is None and by_id[plain["id"]]["app_event_status"] is None
    assert by_id[wake["id"]]["app_event_status"]["state"] == "active"


async def test_readiness_says_whether_this_install_can_receive_events(api, mail, monkeypatch):
    ready = (await api.get("/api/v1/routines/app-event/readiness")).json()
    assert ready == {"composio_configured": True, "can_receive": True, "reason": None, "message": None}
    monkeypatch.setattr(settings, "SUPARAGENT_PUBLIC_URL", "")
    blocked = (await api.get("/api/v1/routines/app-event/readiness")).json()
    assert blocked["can_receive"] is False and blocked["reason"] == "no_public_endpoint" and "relay" in blocked["message"]


async def test_the_routes_need_local_access_and_are_admin_only(scheduler, mail):
    assert required_scope("GET", "/api/v1/routines/app-event/readiness") == "admin"
    assert required_scope("POST", "/api/v1/routines") == "admin" and required_scope("PATCH", "/api/v1/routines/3") == "admin"
    async with client_for(app_for(scheduler), client=("203.0.113.9", 4000)) as remote:
        assert (await remote.get("/api/v1/routines/app-event/readiness")).status_code == 403
        assert (await remote.post("/api/v1/routines", json={"agent_id": 1, "name": "x", "prompt": "p", "app_event": EVENT})).status_code == 403
    assert not [c for c in mail.calls if c[0] != "GET"]

