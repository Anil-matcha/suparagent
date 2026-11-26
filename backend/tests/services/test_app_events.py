"""A routine's new-mail trigger: enable, pause, remove, reconcile and status (BL-177), against a fake Composio."""
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.models import Agent, AppEventDay, AppEventTrigger, Routine
from app.db.models.user_credential import UserCredential
from app.services import agent_store, app_events, composio_service
from app.services import routine_service
from app.services.app_events import AppEventError
from app.services.composio_client import ComposioClient
from tests.services.composio_trigger_fake import SECRET_LENGTH, TriggerFake
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401

KEY = "ak_APPEVENTS_abcdef123456"
ORIGIN = "https://dots.example.com"
ACCOUNT = "ca_g"
CONFIG = {"app": "gmail", "account_id": ACCOUNT, "query": "label:support"}


@pytest.fixture
def fake():
    return TriggerFake()


@pytest.fixture
def make_client(fake):
    return lambda key: ComposioClient(key, transport=httpx.MockTransport(fake.handler))


@pytest.fixture(autouse=True)
def public(monkeypatch):
    monkeypatch.setattr(settings, "SUPARAGENT_PUBLIC_URL", ORIGIN)


@pytest.fixture
async def world(sessions, owner):
    async with sessions() as db:
        agent = await agent_store.create_agent(db, owner_id=owner, name="Support")
        await agent_store.update_agent(db, agent.id, connector_scopes={"gmail": "read"}, connector_account_scopes={"gmail": [ACCOUNT]})
        await composio_service._save(db, owner, {"api_key": KEY, "user_id": "u1", "session_id": "sess_1"})
        routine = await routine_service.create_routine(db, owner, {"agent_id": agent.id, "name": "Triage", "prompt": "Rank the inbox",
                                                                   "schedule_kind": "manual", "schedule_spec": {}, "timezone": "UTC"})
        routine.connected_app_grants = [{"tool": "gmail_fetch_emails", "account_id": ACCOUNT}]
        await db.commit()
    yield agent.id, routine.id
    async with sessions() as db:
        await db.execute(delete(Routine).where(Routine.owner_id == owner))
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


async def load(db, routine_id):
    return await db.get(Routine, routine_id, populate_existing=True)


async def enable(sessions, owner, make_client, routine_id, config=CONFIG):
    async with sessions() as db:
        return await app_events.enable(db, owner, await load(db, routine_id), make_client, config)


# -- the setting ---------------------------------------------------------------------------------------------------------

def test_the_setting_is_filled_in_and_strictly_checked():
    assert app_events.normalize({"account_id": "ca_1"}) == {"app": "gmail", "account_id": "ca_1", "query": "", "debounce_minutes": 5, "max_runs_per_day": 12}
    full = app_events.normalize({"account_id": "ca_1", "query": " label:x ", "debounce_minutes": 10, "max_runs_per_day": 3,
                                 "quiet": {"from": "20:00", "to": "07:00"}})
    assert full["query"] == "label:x" and full["quiet"] == {"from": "20:00", "to": "07:00"}
    for bad in ("x", {}, {"account_id": 5}, {"account_id": "a b"}, {"account_id": "a", "app": "github"}, {"account_id": "a", "extra": 1},
                {"account_id": "a", "query": "x" * 201}, {"account_id": "a", "query": "a\nb"}, {"account_id": "a", "debounce_minutes": 0},
                {"account_id": "a", "debounce_minutes": True}, {"account_id": "a", "max_runs_per_day": 49},
                {"account_id": "a", "quiet": {"from": "20:00"}}, {"account_id": "a", "quiet": {"from": "20:00", "to": "20:00"}},
                {"account_id": "a", "quiet": {"from": "25:00", "to": "07:00"}}):
        with pytest.raises(AppEventError):
            app_events.normalize(bad)


# -- enabling ------------------------------------------------------------------------------------------------------------

async def test_enable_creates_one_webhook_and_one_instance_and_is_idempotent(world, fake, make_client, sessions, owner):
    _, routine_id = world
    row = await enable(sessions, owner, make_client, routine_id)
    assert row.state == "active" and row.query == "label:support" and row.polling_interval == 2
    await enable(sessions, owner, make_client, routine_id)
    assert len(fake.subs) == 1 and len(fake.triggers) == 1
    (sub,) = fake.subs.values()
    assert sub["webhook_url"].startswith(ORIGIN + "/hooks/composio/") and "composio.trigger.message" in sub["enabled_events"]
    (trigger,) = fake.triggers.values()
    assert trigger["trigger_config"] == {"query": "label:support"} and trigger["connected_account_id"] == ACCOUNT
    async with sessions() as db:
        routine = await load(db, routine_id)
        assert routine.app_event["account_id"] == ACCOUNT and routine.app_event["debounce_minutes"] == 5
        route = await app_events.webhook_route_for(db, owner)
        secret = await app_events.webhook_secret_for(db, owner)
        assert sub["webhook_url"].endswith("/" + route) and secret == sub["secret"] and len(secret) == SECRET_LENGTH
        assert (await composio_service.load(db, owner))["session_id"] == "sess_1"   # the saved session survives


@pytest.mark.parametrize("change, code", [
    ("agent_no_scope", "agent_cannot_read_mail"), ("agent_other_account", "agent_cannot_read_mail"),
    ("no_grant", "routine_cannot_read_mail"), ("grant_other_account", "routine_cannot_read_mail"),
    ("draft_two_mailboxes", "invalid_app_grant"), ("no_endpoint", "no_public_endpoint"), ("http_endpoint", "https_required"),
])
async def test_every_precondition_is_refused_in_plain_words_before_any_remote_call(world, fake, make_client, sessions, owner, monkeypatch, change, code):
    agent_id, routine_id = world
    async with sessions() as db:
        if change == "agent_no_scope":
            await agent_store.update_agent(db, agent_id, connector_scopes={}, connector_account_scopes={})
        elif change == "agent_other_account":
            await agent_store.update_agent(db, agent_id, connector_account_scopes={"gmail": ["ca_other"]})
        elif change == "no_grant":
            (await load(db, routine_id)).connected_app_grants = []
        elif change == "grant_other_account":
            (await load(db, routine_id)).connected_app_grants = [{"tool": "gmail_fetch_emails", "account_id": "ca_other"}]
        elif change == "draft_two_mailboxes":
            (await load(db, routine_id)).connected_app_grants = [
                {"tool": "gmail_prepare_checked_reply", "account_id": ACCOUNT}, {"tool": "gmail_fetch_message", "account_id": ACCOUNT},
                {"tool": "gmail_get_profile", "account_id": "ca_other"}]
        await db.commit()
    if change == "no_endpoint":
        monkeypatch.setattr(settings, "SUPARAGENT_PUBLIC_URL", "")
    if change == "http_endpoint":
        monkeypatch.setattr(settings, "SUPARAGENT_PUBLIC_URL", "http://dots.example.com")
    with pytest.raises(AppEventError) as caught:
        await enable(sessions, owner, make_client, routine_id)
    assert caught.value.code == code and len(caught.value.message) > 30
    assert fake.calls == []                       # nothing was sent to Composio, not even a read


async def test_uninterrupted_access_satisfies_the_routine_read_requirement(world, fake, make_client, sessions, owner):
    _, routine_id = world
    async with sessions() as db:
        routine = await load(db, routine_id)
        routine.connected_app_grants, routine.uninterrupted_access = [], True
        await db.commit()
    assert (await enable(sessions, owner, make_client, routine_id)).state == "active"


async def test_a_disconnected_account_and_a_missing_key_are_refused_without_creating_anything(world, fake, make_client, sessions, owner):
    _, routine_id = world
    fake.accounts[0]["status"] = "EXPIRED"
    with pytest.raises(AppEventError) as caught:
        await enable(sessions, owner, make_client, routine_id)
    assert caught.value.code == "account_not_connected" and fake.mutations() == []
    async with sessions() as db:
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()
    with pytest.raises(AppEventError) as caught:
        await enable(sessions, owner, make_client, routine_id)
    assert caught.value.code == "not_configured"


async def test_two_routines_cannot_share_one_remote_instance(world, fake, make_client, sessions, owner):
    agent_id, first = world
    await enable(sessions, owner, make_client, first)
    async with sessions() as db:
        second = await routine_service.create_routine(db, owner, {"agent_id": agent_id, "name": "Other", "prompt": "p", "schedule_kind": "manual",
                                                                  "schedule_spec": {}, "timezone": "UTC"})
        second.connected_app_grants = [{"tool": "gmail_fetch_emails", "account_id": ACCOUNT}]
        await db.commit()
        second_id = second.id
    with pytest.raises(AppEventError) as caught:
        await enable(sessions, owner, make_client, second_id)
    assert caught.value.code == "trigger_in_use" and len(fake.triggers) == 1
    await enable(sessions, owner, make_client, second_id, {**CONFIG, "query": "label:billing"})   # a different search is fine
    assert len(fake.triggers) == 2


async def test_changing_the_search_makes_a_new_instance_and_deletes_the_old_one(world, fake, make_client, sessions, owner):
    _, routine_id = world
    first = await enable(sessions, owner, make_client, routine_id)
    old = first.composio_trigger_id
    second = await enable(sessions, owner, make_client, routine_id, {**CONFIG, "query": "label:vip"})
    assert second.composio_trigger_id != old and list(fake.triggers) == [second.composio_trigger_id]


async def test_a_paused_routine_is_enabled_but_left_paused_at_composio(world, fake, make_client, sessions, owner):
    _, routine_id = world
    async with sessions() as db:
        (await load(db, routine_id)).paused = True
        await db.commit()
    row = await enable(sessions, owner, make_client, routine_id)
    assert row.state == "paused" and next(iter(fake.triggers.values()))["disabled_at"]


# -- the webhook is only ever ours ------------------------------------------------------------------------------------------

async def test_a_foreign_webhook_is_never_touched_even_when_ours_is_cleaned_up(world, fake, make_client, sessions, owner):
    _, routine_id = world
    fake.subs["ws_foreign"] = {"id": "ws_foreign", "webhook_url": "https://other.example.com/events", "version": "V3",
                               "enabled_events": ["composio.trigger.message"], "secret": "z" * 64}
    await enable(sessions, owner, make_client, routine_id)
    assert len(fake.subs) == 2
    async with sessions() as db:
        await app_events.remove(db, owner, await load(db, routine_id), make_client)
    assert list(fake.subs) == ["ws_foreign"]                              # ours went, theirs stayed


async def test_when_composio_allows_only_one_webhook_the_foreign_one_is_kept_and_the_owner_is_told(world, fake, make_client, sessions, owner):
    _, routine_id = world
    fake.allow_many_subs = False
    fake.subs["ws_foreign"] = {"id": "ws_foreign", "webhook_url": "https://other.example.com/events", "version": "V3",
                               "enabled_events": ["composio.trigger.message"], "secret": "z" * 64}
    with pytest.raises(AppEventError) as caught:
        await enable(sessions, owner, make_client, routine_id)
    assert caught.value.code == "webhook_in_use" and list(fake.subs) == ["ws_foreign"] and fake.triggers == {}
    assert not [c for c in fake.calls if c[0] == "DELETE"]


async def test_a_webhook_address_composio_cannot_resolve_is_explained(world, fake, make_client, sessions, owner, monkeypatch):
    _, routine_id = world
    monkeypatch.setattr(settings, "SUPARAGENT_PUBLIC_URL", "https://dots.invalid")
    with pytest.raises(AppEventError) as caught:
        await enable(sessions, owner, make_client, routine_id)
    assert caught.value.code == "webhook_rejected" and "resolves" in caught.value.message and fake.triggers == {}


async def test_our_webhook_changed_outside_the_app_is_not_overwritten(world, fake, make_client, sessions, owner):
    _, routine_id = world
    await enable(sessions, owner, make_client, routine_id)
    next(iter(fake.subs.values()))["webhook_url"] = "https://someone-else.example.com/x"
    with pytest.raises(AppEventError) as caught:
        await enable(sessions, owner, make_client, routine_id, {**CONFIG, "query": "label:vip"})
    assert caught.value.code == "webhook_changed" and next(iter(fake.subs.values()))["webhook_url"] == "https://someone-else.example.com/x"


async def test_saved_webhook_gains_new_status_events_without_losing_extra_events(world, fake, make_client, sessions, owner):
    _, routine_id = world
    await enable(sessions, owner, make_client, routine_id)
    (sub,) = fake.subs.values()
    sub["enabled_events"] = ["composio.trigger.message", "vendor.custom.event"]

    await enable(sessions, owner, make_client, routine_id)

    assert "composio.trigger.disabled" in sub["enabled_events"]
    assert "composio.connected_account.expired" in sub["enabled_events"]
    assert "vendor.custom.event" in sub["enabled_events"]
    assert [method for method, path in fake.calls if method == "PATCH" and path.endswith(sub["id"])]


async def test_our_own_webhook_is_adopted_again_when_the_local_record_was_lost(world, fake, make_client, sessions, owner):
    _, routine_id = world
    await enable(sessions, owner, make_client, routine_id)
    (sub,) = fake.subs.values()
    async with sessions() as db:                                              # a key change rewrites the record without it
        await composio_service._save(db, owner, {"api_key": KEY, "user_id": "u1", "session_id": "sess_1"})
        assert await app_events.webhook_route_for(db, owner) is None
    await enable(sessions, owner, make_client, routine_id)
    assert len(fake.subs) == 1
    async with sessions() as db:
        assert await app_events.webhook_secret_for(db, owner) == sub["secret"]


# -- pause, resume, remove --------------------------------------------------------------------------------------------------

async def test_pause_and_resume_follow_the_routine_and_a_composio_outage_does_not_block_them(world, fake, make_client, sessions, owner):
    _, routine_id = world
    await enable(sessions, owner, make_client, routine_id)
    (trigger,) = fake.triggers.values()
    async with sessions() as db:
        routine = await load(db, routine_id)
        await app_events.set_paused(db, owner, routine, True, make_client)
        assert (await db.scalar(select(AppEventTrigger.state))) == "paused" and trigger["disabled_at"]
        await app_events.set_paused(db, owner, routine, False, make_client)
        assert (await db.scalar(select(AppEventTrigger.state))) == "active" and not trigger["disabled_at"]
        fake.fail["PATCH /trigger_instances/manage/"] = 500
        await app_events.set_paused(db, owner, routine, True, make_client)       # no exception
        row = (await db.execute(select(AppEventTrigger))).scalar_one()
        assert row.state == "active" and row.last_error


async def test_remove_deletes_the_instance_the_row_and_the_last_webhook(world, fake, make_client, sessions, owner):
    _, routine_id = world
    await enable(sessions, owner, make_client, routine_id)
    async with sessions() as db:
        await app_events.remove(db, owner, await load(db, routine_id), make_client)
        assert (await load(db, routine_id)).app_event is None
        assert (await db.execute(select(AppEventTrigger))).first() is None
        assert await app_events.webhook_route_for(db, owner) is None
    assert fake.triggers == {} and fake.subs == {}


async def test_the_webhook_stays_while_another_routine_still_listens(world, fake, make_client, sessions, owner):
    agent_id, first = world
    await enable(sessions, owner, make_client, first)
    async with sessions() as db:
        second = await routine_service.create_routine(db, owner, {"agent_id": agent_id, "name": "B", "prompt": "p", "schedule_kind": "manual",
                                                                  "schedule_spec": {}, "timezone": "UTC"})
        second.connected_app_grants = [{"tool": "gmail_fetch_emails", "account_id": ACCOUNT}]
        await db.commit()
        second_id = second.id
    await enable(sessions, owner, make_client, second_id, {**CONFIG, "query": "label:b"})
    async with sessions() as db:
        await app_events.remove(db, owner, await load(db, first), make_client)
    assert len(fake.subs) == 1 and len(fake.triggers) == 1


async def test_a_failed_remote_delete_keeps_a_removed_row_that_a_later_reconcile_finishes(world, fake, make_client, sessions, owner):
    _, routine_id = world
    await enable(sessions, owner, make_client, routine_id)
    fake.fail["DELETE /trigger_instances/manage/"] = 500
    async with sessions() as db:
        await app_events.remove(db, owner, await load(db, routine_id), make_client)
        row = (await db.execute(select(AppEventTrigger))).scalar_one()
        assert row.state == "removed" and row.last_error and (await load(db, routine_id)).app_event is None
    assert len(fake.triggers) == 1
    fake.fail.clear()
    async with sessions() as db:
        report = await app_events.reconcile(db, owner, make_client)
        assert report["retried_deletes"] == 1 and (await db.execute(select(AppEventTrigger))).first() is None
    assert fake.triggers == {}


async def test_deleting_a_routine_remembers_an_instance_composio_would_not_delete(world, fake, make_client, sessions, owner):
    _, routine_id = world
    await enable(sessions, owner, make_client, routine_id)
    (trigger_id,) = fake.triggers
    fake.fail["DELETE /trigger_instances/manage/"] = 500
    async with sessions() as db:
        await app_events.remove(db, owner, await load(db, routine_id), make_client, deleting=True)
        assert (await composio_service.load(db, owner))["pending_trigger_deletes"] == [trigger_id]
    fake.fail.clear()
    async with sessions() as db:
        report = await app_events.reconcile(db, owner, make_client)
        assert report["retried_deletes"] == 1 and (await composio_service.load(db, owner))["pending_trigger_deletes"] == []
    assert fake.triggers == {}


# -- reconcile ----------------------------------------------------------------------------------------------------------------

async def test_reconcile_with_nothing_to_compare_makes_no_remote_call(world, fake, make_client, sessions, owner):
    async with sessions() as db:
        assert (await app_events.reconcile(db, owner, make_client))["errors"] == 0
    assert fake.calls == []


async def test_reconcile_recreates_a_deleted_instance_and_re_enables_a_disabled_one_and_leaves_foreign_ones_alone(world, fake, make_client, sessions, owner):
    _, routine_id = world
    await enable(sessions, owner, make_client, routine_id)
    fake.triggers["ti_foreign"] = {"id": "ti_foreign", "connected_account_id": "ca_x", "trigger_config": {}, "triggerName": "GMAIL_NEW_GMAIL_MESSAGE",
                                   "disabled_at": None, "polling_interval": 2}
    (own,) = [t for t in fake.triggers if t != "ti_foreign"]
    fake.triggers[own]["disabled_at"] = "2026-11-26T00:00:00Z"
    async with sessions() as db:
        report = await app_events.reconcile(db, owner, make_client)
        assert report["re_enabled"] == 1 and report["foreign"] == 1 and not fake.triggers[own]["disabled_at"]
    del fake.triggers[own]
    async with sessions() as db:
        report = await app_events.reconcile(db, owner, make_client)
        row = (await db.execute(select(AppEventTrigger))).scalar_one()
        assert report["recreated"] == 1 and row.composio_trigger_id in fake.triggers and row.state == "active"
    assert "ti_foreign" in fake.triggers


async def test_reconcile_recreates_our_webhook_if_it_was_deleted_in_composio(world, fake, make_client, sessions, owner):
    _, routine_id = world
    await enable(sessions, owner, make_client, routine_id)
    (url,) = [s["webhook_url"] for s in fake.subs.values()]
    fake.subs.clear()
    async with sessions() as db:
        await app_events.reconcile(db, owner, make_client)
    assert [s["webhook_url"] for s in fake.subs.values()] == [url]


# -- errors and status ---------------------------------------------------------------------------------------------------------

async def test_losing_access_marks_the_trigger_in_error_and_pauses_it_remotely_but_keeps_the_routine(world, fake, make_client, sessions, owner):
    agent_id, routine_id = world
    await enable(sessions, owner, make_client, routine_id)
    async with sessions() as db:
        await agent_store.update_agent(db, agent_id, connector_scopes={}, connector_account_scopes={})
    async with sessions() as db:
        assert await app_events.on_access_changed(db, agent_id, make_client) == 1
        row = (await db.execute(select(AppEventTrigger))).scalar_one()
        assert row.state == "error" and "not allowed to read" in row.last_error and await load(db, routine_id)
    assert next(iter(fake.triggers.values()))["disabled_at"]
    async with sessions() as db:
        assert await app_events.on_access_changed(db, agent_id, make_client) == 0     # already in error


async def test_status_reports_counts_today_and_whether_events_can_arrive(world, fake, make_client, sessions, owner, monkeypatch):
    _, routine_id = world
    async with sessions() as db:
        assert (await app_events.status_for(db, await load(db, routine_id)))["state"] == "off"
    row = await enable(sessions, owner, make_client, routine_id)
    async with sessions() as db:
        db.add(AppEventDay(trigger_id=row.id, day=datetime.now(UTC).date(), received=7, started=2, merged=3, dropped_cap=1))
        await db.commit()
        status = await app_events.status_for(db, await load(db, routine_id))
        assert status["state"] == "active" and status["can_receive"] is True and status["reason"] is None
        assert status["today"]["received"] == 7 and status["today"]["merged"] == 3 and status["today"]["dropped_cap"] == 1
        assert set(status["today"]) == set(app_events.COUNTERS)
        monkeypatch.setattr(settings, "SUPARAGENT_PUBLIC_URL", "")
        gone = await app_events.status_for(db, await load(db, routine_id))
        assert gone["can_receive"] is False and gone["reason"] == "no_public_endpoint" and "relay" in gone["message"]


async def test_the_signing_secret_is_in_no_status_and_no_error(world, fake, make_client, sessions, owner):
    _, routine_id = world
    await enable(sessions, owner, make_client, routine_id)
    (sub,) = fake.subs.values()
    async with sessions() as db:
        status = await app_events.status_for(db, await load(db, routine_id))
        text = repr(status) + repr(await load(db, routine_id)) + repr((await db.execute(select(AppEventTrigger))).scalar_one())
        assert sub["secret"] not in text and KEY not in text
