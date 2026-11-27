"""A new-mail event becomes at most one queued routine run (BL-177): dedupe, the share of the mailbox, caps, one
coalesced wake with a debounce and quiet hours, a follow-up that is never lost, and ids only in everything stored.

Events here are the reduced dicts of app/composio_events.extract; the real scheduler starts the queued runs."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app import composio_events, gmail_share
from app.db.models import Agent, AppEventDay, AppEventReceipt, AppEventTrigger, Notification, Routine, RoutineRun
from app.services import routine_service as service
from tests.services.test_routine_service import (  # noqa: F401
    T0, Clock, agent, agent_args, clock, local_enabled, make_routine, reload, routine_cleanup, runs_of, scheduler, settled)
from tests.services.thread_harness import cleanup, cli, owner, rt, sessions, wait_until  # noqa: F401

SEED = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
ACCOUNT = "ca_g"
TRIGGER = "ti_mail_1"
ROUTE = "r" * 32
TEXT = "THE_MESSAGE_TEXT_THAT_MUST_NEVER_BE_STORED"
SENDER = "sender-that-must-not-be-stored@example.com"


@pytest.fixture(autouse=True)
async def app_event_cleanup(sessions, owner, routine_cleanup):
    service._POISON.clear()
    yield
    async with sessions() as db:
        ids = select(AppEventTrigger.id).where(AppEventTrigger.owner_id == owner)
        await db.execute(delete(AppEventDay).where(AppEventDay.trigger_id.in_(ids)))
        await db.execute(delete(AppEventReceipt).where(AppEventReceipt.trigger_id.in_(ids)))
        await db.execute(delete(AppEventTrigger).where(AppEventTrigger.owner_id == owner))
        await db.commit()


def event(n=1, thread="aa11", **over):
    return {"kind": "message", "event_id": f"evt_{n}", "trigger_id": TRIGGER, "connected_account_id": ACCOUNT,
            "thread_id": thread, "message_id": "bb22", "received_at": None, "bad_ids": False, **over}


async def mail_routine(sessions, owner, agent, clock, *, config=None, shares=None, state="active", paused=False, **data):
    routine = await make_routine(sessions, owner, agent, clock, schedule_kind="manual", schedule_spec={}, **data)
    async with sessions() as db:
        row = await db.get(Routine, routine.id)
        row.app_event = {"app": "gmail", "account_id": ACCOUNT, "query": "label:support", **(config or {})}
        row.paused = paused
        if shares is not None:
            (await db.get(Agent, agent.id)).connector_shares = shares
        db.add(AppEventTrigger(owner_id=owner, routine_id=row.id, composio_trigger_id=TRIGGER, composio_account_id=ACCOUNT,
                               slug="GMAIL_NEW_GMAIL_MESSAGE", state=state))
        await db.commit()
    return routine


async def accept(sessions, owner, ev, now=T0, **kw):
    async with sessions() as db:
        return await service.accept_app_event(db, ROUTE, ev, now, owner_id=kw.pop("owner_id", owner), **kw)


async def day(sessions):
    async with sessions() as db:
        row = (await db.execute(select(AppEventDay))).scalars().first()
        return None if row is None else {c: getattr(row, c) for c in
                ("received", "started", "merged", "duplicate", "outside_share", "rejected", "unrouted", "dropped_cap", "dropped_quiet")}


def counts(**over):
    base = dict(received=0, started=0, merged=0, duplicate=0, outside_share=0, rejected=0, unrouted=0, dropped_cap=0, dropped_quiet=0)
    return {**base, **over}


def share(percent):
    return {"gmail": {ACCOUNT: {"percent": percent, "seed": SEED, "pins": []}}}


def hexid(rule, wanted):
    return next(f"{n:016x}" for n in range(1, 100_000) if gmail_share.in_share(rule, f"{n:016x}") is wanted)


# -- one event, one queued wake ---------------------------------------------------------------------------------------

async def test_an_event_queues_one_run_after_the_debounce_with_the_thread_id_in_its_prompt(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock, prompt="Rank the support threads")
    result = await accept(sessions, owner, event())
    assert result["outcome"] == "queued"
    (run,) = await runs_of(sessions, routine.id)
    assert (run.trigger, run.state, run.id) == ("app_event", "queued", result["run_id"])
    assert run.scheduled_for == T0 + timedelta(minutes=5)
    assert run.prompt.startswith('[Routine "Daily report", started because new mail arrived at 2026-10-17 09:00 UTC. Do this now:]')
    assert "Rank the support threads" in run.prompt
    assert service.parse_mail_paragraph(run.prompt) == (["aa11"], 0)
    assert await day(sessions) == counts(received=1, started=1)


async def test_the_debounce_comes_from_the_routine(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock, config={"debounce_minutes": 2})
    await accept(sessions, owner, event())
    assert (await runs_of(sessions, routine.id))[0].scheduled_for == T0 + timedelta(minutes=2)


@pytest.mark.parametrize("bad", [0, -1, 999, "5", True, 2.5, None])
async def test_a_bad_debounce_setting_falls_back_to_the_default(sessions, owner, agent, clock, bad):
    routine = await mail_routine(sessions, owner, agent, clock, config={"debounce_minutes": bad})
    await accept(sessions, owner, event())
    assert (await runs_of(sessions, routine.id))[0].scheduled_for == T0 + timedelta(minutes=5)


# -- what never gets stored ------------------------------------------------------------------------------------------

async def test_no_message_text_sender_or_subject_survives_into_the_run_or_the_counters(sessions, owner, agent, clock):
    await mail_routine(sessions, owner, agent, clock)
    envelope = {"id": "evt_1", "type": "composio.trigger.message", "metadata": {"trigger_id": TRIGGER, "connected_account_id": ACCOUNT},
                "data": {"thread_id": "aa11", "message_id": "bb22", "message_text": TEXT, "sender": SENDER, "subject": TEXT,
                         "preview": {"body": TEXT}}}
    reduced = composio_events.extract(envelope, "evt_1")
    await accept(sessions, owner, reduced)
    async with sessions() as db:
        stored = [r.prompt for r in (await db.execute(select(RoutineRun))).scalars()]
        stored += [str(vars(d)) for d in (await db.execute(select(AppEventDay))).scalars()]
        stored += [r.event_hash for r in (await db.execute(select(AppEventReceipt))).scalars()]
        stored += [str(t.last_error) for t in (await db.execute(select(AppEventTrigger))).scalars()]
    blob = "\n".join(stored)
    assert TEXT not in blob and SENDER not in blob and "evt_1" not in blob  # even the event id is only a hash


# -- the trigger ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("make", [
    dict(state="removed"), dict(state="paused"), dict(state="error"), dict(paused=True),
])
async def test_an_inactive_trigger_or_paused_routine_is_unrouted_and_starts_nothing(sessions, owner, agent, clock, make):
    routine = await mail_routine(sessions, owner, agent, clock, **make)
    result = await accept(sessions, owner, event())
    assert result["outcome"] == "unrouted" and await runs_of(sessions, routine.id) == []


async def test_an_unknown_trigger_or_another_owners_trigger_is_unrouted(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    assert (await accept(sessions, owner, event(trigger_id="ti_unknown")))["outcome"] == "unrouted"
    assert (await accept(sessions, owner, event(), owner_id="someone-else"))["outcome"] == "unrouted"
    assert (await accept(sessions, owner, {"kind": "message", "event_id": "e", "trigger_id": None}))["outcome"] == "unrouted"
    assert await runs_of(sessions, routine.id) == []


async def test_an_archived_agent_or_a_routine_without_an_event_setting_is_unrouted(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    async with sessions() as db:
        (await db.get(Routine, routine.id)).app_event = None
        await db.commit()
    assert (await accept(sessions, owner, event()))["outcome"] == "unrouted"
    async with sessions() as db:
        (await db.get(Routine, routine.id)).app_event = {"app": "gmail"}
        (await db.get(Agent, agent.id)).archived_at = T0
        await db.commit()
    assert (await accept(sessions, owner, event(2)))["outcome"] == "unrouted"
    assert await runs_of(sessions, routine.id) == []


# -- dedupe -----------------------------------------------------------------------------------------------------------

async def test_the_same_event_twice_is_one_run_and_a_counted_duplicate(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    assert (await accept(sessions, owner, event()))["outcome"] == "queued"
    assert (await accept(sessions, owner, event()))["outcome"] == "duplicate"
    assert len(await runs_of(sessions, routine.id)) == 1
    assert await day(sessions) == counts(received=2, started=1, duplicate=1)


async def test_a_dropped_event_is_remembered_so_its_retry_is_a_duplicate(sessions, owner, agent, clock):
    await mail_routine(sessions, owner, agent, clock, shares=share(5))
    out = hexid(gmail_share.rule_from_json(share(5)["gmail"][ACCOUNT]), False)
    assert (await accept(sessions, owner, event(thread=out)))["outcome"] == "outside_share"
    assert (await accept(sessions, owner, event(thread=out)))["outcome"] == "duplicate"


async def test_receipts_older_than_a_week_are_forgotten(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    await accept(sessions, owner, event())
    async with sessions() as db:
        (await db.get(RoutineRun, (await runs_of(sessions, routine.id))[0].id)).state = "completed"
        await db.commit()
    later = T0 + timedelta(days=8)
    assert (await accept(sessions, owner, event(), later))["outcome"] == "queued"  # same event id, a week on


# -- id hygiene and the share of the mailbox ------------------------------------------------------------------------

async def test_a_malformed_id_refuses_the_event(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    assert (await accept(sessions, owner, event(thread=None, bad_ids=True)))["outcome"] == "rejected"
    assert await runs_of(sessions, routine.id) == [] and (await day(sessions))["rejected"] == 1


async def test_a_thread_inside_the_share_starts_a_run_and_one_outside_is_counted_and_dropped(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock, shares=share(5))
    rule = gmail_share.rule_from_json(share(5)["gmail"][ACCOUNT])
    inside, outside = hexid(rule, True), hexid(rule, False)
    assert (await accept(sessions, owner, event(1, thread=outside)))["outcome"] == "outside_share"
    assert await runs_of(sessions, routine.id) == []
    assert (await accept(sessions, owner, event(2, thread=inside)))["outcome"] == "queued"
    (run,) = await runs_of(sessions, routine.id)
    assert inside in run.prompt and outside not in run.prompt   # the dropped thread id is not stored anywhere
    assert await day(sessions) == counts(received=2, started=1, outside_share=1)


async def test_a_pinned_thread_is_always_inside(sessions, owner, agent, clock):
    rule = gmail_share.rule_from_json(share(5)["gmail"][ACCOUNT])
    outside = hexid(rule, False)
    pinned = {"gmail": {ACCOUNT: {"percent": 5, "seed": SEED, "pins": [outside]}}}
    await mail_routine(sessions, owner, agent, clock, shares=pinned)
    assert (await accept(sessions, owner, event(thread=outside)))["outcome"] == "queued"


async def test_without_a_thread_id_a_share_fails_closed_and_the_whole_mailbox_still_wakes(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock, shares=share(50))
    assert (await accept(sessions, owner, event(thread=None)))["outcome"] == "outside_share"
    async with sessions() as db:
        (await db.get(Agent, agent.id)).connector_shares = {}
        await db.commit()
    assert (await accept(sessions, owner, event(2, thread=None)))["outcome"] == "queued"
    (run,) = await runs_of(sessions, routine.id)
    assert service.parse_mail_paragraph(run.prompt) == ([], 1) and "check the inbox" in run.prompt


@pytest.mark.parametrize("damaged", [{"gmail": {ACCOUNT: {"percent": 0}}}, {"gmail": {ACCOUNT: "x"}}, {"gmail": "x"}])
async def test_a_damaged_share_denies_and_never_wakes(sessions, owner, agent, clock, damaged):
    routine = await mail_routine(sessions, owner, agent, clock, shares=damaged)
    assert (await accept(sessions, owner, event()))["outcome"] == "outside_share"
    assert await runs_of(sessions, routine.id) == []


async def test_a_share_for_another_account_does_not_limit_this_one(sessions, owner, agent, clock):
    await mail_routine(sessions, owner, agent, clock, shares={"gmail": {"ca_other": {"percent": 1, "seed": SEED, "pins": []}}})
    assert (await accept(sessions, owner, event()))["outcome"] == "queued"


# -- caps ---------------------------------------------------------------------------------------------------------------

async def test_events_past_the_daily_cap_are_counted_and_answered_normally(sessions, owner, agent, clock, monkeypatch):
    monkeypatch.setattr(service, "APP_EVENT_MAX_PER_DAY", 3)
    routine = await mail_routine(sessions, owner, agent, clock)
    outcomes = [(await accept(sessions, owner, event(n, thread=f"a{n}")))["outcome"] for n in range(1, 6)]
    assert outcomes == ["queued", "merged", "merged", "dropped_cap", "dropped_cap"]
    assert await day(sessions) == counts(received=5, started=1, merged=2, dropped_cap=2)
    assert len(await runs_of(sessions, routine.id)) == 1


async def test_runs_past_the_daily_run_cap_are_counted_and_the_schedule_is_untouched(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock, config={"max_runs_per_day": 1})
    assert (await accept(sessions, owner, event(1)))["outcome"] == "queued"
    async with sessions() as db:
        (await db.get(RoutineRun, (await runs_of(sessions, routine.id))[0].id)).state = "completed"
        await db.commit()
    assert (await accept(sessions, owner, event(2, thread="b2")))["outcome"] == "dropped_cap"
    assert len(await runs_of(sessions, routine.id)) == 1 and (await day(sessions))["dropped_cap"] == 1


async def test_the_counters_start_afresh_each_utc_day(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock, config={"max_runs_per_day": 1})
    await accept(sessions, owner, event(1))
    async with sessions() as db:
        (await db.get(RoutineRun, (await runs_of(sessions, routine.id))[0].id)).state = "completed"
        await db.commit()
    assert (await accept(sessions, owner, event(2, thread="b2"), T0 + timedelta(days=1)))["outcome"] == "queued"


# -- coalescing -------------------------------------------------------------------------------------------------------

async def test_a_second_event_merges_into_the_queued_wake_and_ids_are_deduplicated(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    await accept(sessions, owner, event(1, thread="a1"))
    second = await accept(sessions, owner, event(2, thread="a2"), T0 + timedelta(minutes=1))
    again = await accept(sessions, owner, event(3, thread="a1"), T0 + timedelta(minutes=2))  # another message, same thread
    assert second["outcome"] == again["outcome"] == "merged"
    (run,) = await runs_of(sessions, routine.id)
    assert second["run_id"] == run.id
    assert service.parse_mail_paragraph(run.prompt) == (["a1", "a2"], 0)
    assert run.scheduled_for == T0 + timedelta(minutes=5)   # the first debounce stands; later mail does not push it out
    assert run.prompt.count("started because new mail arrived") == 2   # head line + paragraph; the paragraph was replaced, not repeated
    assert (await day(sessions))["merged"] == 2


async def test_only_twenty_ids_are_listed_then_a_count(sessions, owner, agent, clock, monkeypatch):
    monkeypatch.setattr(service, "APP_EVENT_MAX_IDS", 3)
    routine = await mail_routine(sessions, owner, agent, clock)
    for n in range(1, 6):
        await accept(sessions, owner, event(n, thread=f"a{n}"))
    await accept(sessions, owner, event(6, thread=None))
    (run,) = await runs_of(sessions, routine.id)
    assert service.parse_mail_paragraph(run.prompt) == (["a1", "a2", "a3"], 3)
    assert "and 3 more notifications" in run.prompt


def test_the_paragraph_round_trips():
    for ids, more in [(["a1"], 0), (["a1", "b2", "c3"], 4), ([], 1), ([], 5), (["a1"], 1)]:
        assert service.parse_mail_paragraph(service.mail_paragraph(ids, more)) == (ids, more)
    assert service.parse_mail_paragraph("no paragraph here") is None and service.parse_mail_paragraph(None) is None


# -- quiet hours ------------------------------------------------------------------------------------------------------

QUIET = {"from": "20:00", "to": "07:00"}


@pytest.mark.parametrize("zone, now_utc, expected_utc", [
    ("UTC", (2026, 10, 17, 21, 0), (2026, 10, 18, 7, 0)),            # evening: held until morning
    ("UTC", (2026, 10, 18, 3, 0), (2026, 10, 18, 7, 0)),             # small hours: the same morning
    ("UTC", (2026, 10, 17, 12, 0), (2026, 10, 17, 12, 0)),           # daytime: untouched
    ("UTC", (2026, 10, 17, 7, 0), (2026, 10, 17, 7, 0)),             # the window is [from, to)
    ("Asia/Kolkata", (2026, 10, 17, 20, 0), (2026, 10, 18, 1, 30)),  # 01:30 IST is quiet; 07:00 IST is 01:30 UTC
    ("Asia/Kolkata", (2026, 10, 17, 9, 0), (2026, 10, 17, 9, 0)),    # 14:30 IST
])
def test_quiet_hours_move_a_wake_to_the_end_of_the_window_in_the_routines_zone(zone, now_utc, expected_utc):
    assert service.after_quiet_hours(datetime(*now_utc, tzinfo=UTC), QUIET, zone) == datetime(*expected_utc, tzinfo=UTC)


@pytest.mark.parametrize("quiet", [None, {}, {"from": "20:00"}, {"from": "25:00", "to": "07:00"}, {"from": "08:00", "to": "08:00"}, "x"])
def test_a_missing_or_unusable_quiet_setting_changes_nothing(quiet):
    moment = datetime(2026, 10, 17, 22, 0, tzinfo=UTC)
    assert service.after_quiet_hours(moment, quiet, "UTC") == moment


async def test_overnight_mail_becomes_one_morning_run(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock, config={"quiet": QUIET})
    night = datetime(2026, 10, 17, 22, 0, tzinfo=UTC)
    await accept(sessions, owner, event(1, thread="a1"), night)
    await accept(sessions, owner, event(2, thread="a2"), night + timedelta(hours=3))
    (run,) = await runs_of(sessions, routine.id)
    assert run.scheduled_for == datetime(2026, 10, 18, 7, 0, tzinfo=UTC)
    assert service.parse_mail_paragraph(run.prompt) == (["a1", "a2"], 0)


async def test_two_wakes_never_share_a_time_slot(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock, config={"quiet": QUIET})
    await accept(sessions, owner, event(1, thread="a1"), datetime(2026, 10, 17, 22, 0, tzinfo=UTC))
    async with sessions() as db:
        (await db.get(RoutineRun, (await runs_of(sessions, routine.id))[0].id)).state = "completed"
        await db.commit()
    await accept(sessions, owner, event(2, thread="a2"), datetime(2026, 10, 17, 23, 0, tzinfo=UTC))   # also lands on 07:00
    first, second = await runs_of(sessions, routine.id)
    assert second.scheduled_for == first.scheduled_for + timedelta(seconds=1)


# -- starting the wake --------------------------------------------------------------------------------------------------

async def test_a_wake_waits_for_its_debounce_then_starts_like_a_scheduled_run(scheduler, sessions, owner, agent, clock, cli):
    grants = [{"tool": "gmail_fetch_emails", "account_id": ACCOUNT}]
    routine = await mail_routine(sessions, owner, agent, clock, prompt="Rank the threads")
    async with sessions() as db:
        (await db.get(Routine, routine.id)).connected_app_grants = grants
        await db.commit()
    await accept(sessions, owner, event(), clock())
    await scheduler._start_queued()
    assert (await runs_of(sessions, routine.id))[0].state == "queued"          # still inside the debounce
    clock.advance(minutes=5, seconds=1)
    await scheduler._start_queued()
    (run,) = await runs_of(sessions, routine.id)
    assert run.state == "running" and run.thread_id is not None
    assert run.connected_app_grants == grants and run.uninterrupted_access is False   # the access of a scheduled run
    run = await settled(scheduler, sessions, run.id)
    assert run.state == "completed"
    assert "started because new mail arrived" in cli.log("prompt")[0]["prompt"]


async def test_a_wake_during_an_active_run_waits_for_it_and_is_not_lost(scheduler, sessions, owner, agent, clock, cli):
    routine = await mail_routine(sessions, owner, agent, clock)
    async with sessions() as db:
        db.add(RoutineRun(routine_id=routine.id, trigger="manual", state="running", started_at=T0, scheduled_for=T0))
        await db.commit()
    await accept(sessions, owner, event(), clock())
    clock.advance(minutes=10)
    await scheduler._start_queued()
    running, wake = await runs_of(sessions, routine.id)
    assert (running.state, wake.state, wake.trigger) == ("running", "queued", "app_event")
    async with sessions() as db:
        (await db.get(RoutineRun, running.id)).state = "completed"
        await db.commit()
    await scheduler._start_queued()
    assert (await runs_of(sessions, routine.id))[1].state == "running"
    await settled(scheduler, sessions, wake.id)


async def test_a_wake_for_a_routine_paused_meanwhile_is_skipped(scheduler, sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    await accept(sessions, owner, event(), clock())
    async with sessions() as db:
        (await db.get(Routine, routine.id)).paused = True
        await db.commit()
    clock.advance(minutes=6)
    await scheduler._start_queued()
    (run,) = await runs_of(sessions, routine.id)
    assert run.state == "skipped" and "paused" in run.error


async def test_a_queued_wake_is_not_marked_missed_at_startup(scheduler, sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    await accept(sessions, owner, event(), clock())
    clock.advance(days=2)
    await scheduler.recover()
    assert (await runs_of(sessions, routine.id))[0].state == "queued"


# -- status events -----------------------------------------------------------------------------------------------------

async def test_a_disabled_trigger_stops_waking_and_the_owner_is_told_once(sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    status = {"kind": "status", "status": "trigger_disabled", "event_id": "evt_s", "trigger_id": TRIGGER, "connected_account_id": None}
    result = await accept(sessions, owner, status)
    assert result["outcome"] == "status" and result["routine_ids"] == [routine.id]
    async with sessions() as db:
        trigger = (await db.execute(select(AppEventTrigger))).scalar_one()
        notes = list((await db.execute(select(Notification).where(Notification.owner_id == owner))).scalars())
    assert trigger.state == "error" and "switched this trigger off" in trigger.last_error
    assert len(notes) == 1 and notes[0].routine_id == routine.id and "schedule" in notes[0].body
    assert (await accept(sessions, owner, status))["routine_ids"] == []     # already stopped: no second notice
    assert (await accept(sessions, owner, event()))["outcome"] == "unrouted"


async def test_an_expired_account_stops_the_triggers_that_read_it(sessions, owner, agent, clock):
    await mail_routine(sessions, owner, agent, clock)
    status = {"kind": "status", "status": "account_expired", "event_id": "evt_s", "trigger_id": None, "connected_account_id": ACCOUNT}
    assert (await accept(sessions, owner, {**status, "connected_account_id": "ca_other"}))["routine_ids"] == []
    assert len((await accept(sessions, owner, status))["routine_ids"]) == 1
    async with sessions() as db:
        assert "signed in again" in (await db.execute(select(AppEventTrigger))).scalar_one().last_error


# -- an event that keeps failing --------------------------------------------------------------------------------------

async def test_an_event_that_keeps_failing_is_dropped_after_five_tries_and_counted(sessions, owner, agent, clock, monkeypatch):
    routine = await mail_routine(sessions, owner, agent, clock)
    real = service._accept_mail_event

    async def boom(*args, **kwargs):
        raise RuntimeError("could not process")

    monkeypatch.setattr(service, "_accept_mail_event", boom)
    for _ in range(4):
        with pytest.raises(RuntimeError):
            await accept(sessions, owner, event())
    assert (await accept(sessions, owner, event()))["outcome"] == "dropped_poison"
    monkeypatch.setattr(service, "_accept_mail_event", real)
    assert (await day(sessions))["rejected"] == 1
    assert (await accept(sessions, owner, event()))["outcome"] == "duplicate"     # its retry is remembered
    assert await runs_of(sessions, routine.id) == []
