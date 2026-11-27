"""Support threads waiting past the owner's answer time (BL-176): the collector and the Needs You item.

Real runtime, real run_tool and share rule, the shadow report's Gmail fake serving the threads each test describes."""
import json
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services import support_unanswered, support_windows
from app.team import needs_you
from tests.services.test_composio_bridge import world  # noqa: F401
from tests.services.test_composio_gmail_share import ACCOUNT, entry, grant, rule, thread_in
from tests.services.test_shadow_report import (  # noqa: F401
    CUSTOMER, ME, NOW, ReportFake, clean, fake, message, predict, ready)
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401

UTC_ZONE = ZoneInfo("UTC")
SECRET = "BODY_TEXT_THAT_MUST_NEVER_REACH_THE_CARD"


async def collect(sessions, owner, now=NOW, answered=None):
    async with sessions() as db:
        return await needs_you.collect_support(db, owner, now=now, zone=UTC_ZONE, answered=answered if answered is not None else set())


def fetches(fake, slug="GMAIL_FETCH_MESSAGE_BY_THREAD_ID"):
    return [e["arguments"].get("thread_id") for e in fake.executes if e.get("slug") == slug]


async def mark(sessions, owner, agent, fake, thread, hours_ago, category, *, owner_replied=False, rank=1):
    at = NOW - timedelta(hours=hours_ago)
    await predict(sessions, owner, agent, thread, rank, at, category=category, turn=abs(hash(thread)) % 10**9)
    thread_messages = [message(CUSTOMER, at - timedelta(minutes=30), SECRET, n=1)]
    if owner_replied:
        thread_messages.append(message(ME, at + timedelta(minutes=20), SECRET, n=2))
    fake.threads[thread] = thread_messages


async def test_one_item_per_agent_for_threads_past_their_answer_time(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    await mark(sessions, owner, agent, fake, "urgent1", 6, "angry")        # 6h > 4h urgent window
    await mark(sessions, owner, agent, fake, "slow1", 30, "question")      # 30 working hours > 24
    await mark(sessions, owner, agent, fake, "fresh1", 2, "angry")         # not overdue yet: never read
    await mark(sessions, owner, agent, fake, "fresh2", 20, "question")     # not overdue yet: never read
    (item,) = await collect(sessions, owner)
    assert item.kind == "support_unanswered" and item.key == f"support_unanswered:{agent.id}"
    assert item.title == "2 support threads are waiting longer than your answer time"
    assert "angry urgent1" in item.detail and "question slow1" in item.detail and "fresh" not in item.detail
    assert "4h for urgent, 24h for the rest" in item.detail
    assert sorted(fetches(fake)) == ["slow1", "urgent1"]                    # only the overdue threads were read
    assert SECRET not in json.dumps(item.public())                          # ids, categories and times only


async def test_a_single_thread_reads_in_the_singular(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    await mark(sessions, owner, agent, fake, "only1", 6, "blocked")
    (item,) = await collect(sessions, owner)
    assert item.title == "1 support thread is waiting longer than your answer time"


async def test_it_leaves_when_the_owner_answers_and_the_answered_thread_is_not_read_again(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    await mark(sessions, owner, agent, fake, "a1", 6, "angry")
    await mark(sessions, owner, agent, fake, "b1", 7, "angry", owner_replied=True)
    answered: set = set()
    (item,) = await collect(sessions, owner, answered=answered)
    assert item.title.startswith("1 support thread") and "a1" in item.detail and "b1" not in item.detail
    assert answered == {(ACCOUNT, "b1")}
    before = fetches(fake).count("b1")
    await collect(sessions, owner, answered=answered)
    assert fetches(fake).count("b1") == before                                # remembered: not read again
    fake.threads["a1"].append(message(ME, NOW - timedelta(hours=1), "done", n=9))   # the owner answers the last one
    assert await collect(sessions, owner, answered=answered) == []


async def test_time_alone_never_clears_it(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    await mark(sessions, owner, agent, fake, "old1", 6, "angry")
    assert len(await collect(sessions, owner)) == 1
    later = NOW + timedelta(days=9)                                           # still inside the look-back
    (item,) = await collect(sessions, owner, now=later)
    assert "old1" in item.detail


async def test_marks_older_than_the_look_back_are_no_longer_looked_at(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    await mark(sessions, owner, agent, fake, "ancient1", support_unanswered.LOOKBACK_DAYS * 24 + 24, "angry")
    assert await collect(sessions, owner) == [] and fetches(fake) == []


async def test_without_gmail_access_nothing_is_read_and_nothing_is_claimed(world, fake, sessions, owner):
    runtime, engine, agent = world
    await mark(sessions, owner, agent, fake, "x1", 6, "angry")               # the agent only has github access
    assert await collect(sessions, owner) == []
    assert fake.executes == []


async def test_a_thread_the_share_hides_is_neither_read_nor_reported(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    await grant(sessions, agent, {"gmail": {ACCOUNT: entry(5)}})
    hidden, shown = thread_in(rule(5), False), thread_in(rule(5), True)
    await mark(sessions, owner, agent, fake, hidden, 6, "angry")
    await mark(sessions, owner, agent, fake, shown, 7, "angry")
    (item,) = await collect(sessions, owner)
    assert shown in item.detail and hidden not in item.detail
    assert hidden not in fetches(fake)                                        # refused before any request


async def test_a_failed_read_is_not_reported_but_the_others_are(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    await mark(sessions, owner, agent, fake, "bad1", 6, "angry")
    await mark(sessions, owner, agent, fake, "good1", 7, "angry")
    fake.broken.add("bad1")
    (item,) = await collect(sessions, owner)
    assert "good1" in item.detail and "bad1" not in item.detail


async def test_reads_are_capped_and_the_rest_is_counted(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    total = support_unanswered.MAX_READS + 5
    for n in range(total):
        await mark(sessions, owner, agent, fake, f"t{n:03d}", 6 + n * 0.01, "angry", rank=n + 1)
    (item,) = await collect(sessions, owner)
    assert len(fetches(fake)) == support_unanswered.MAX_READS
    assert item.title.startswith(f"{support_unanswered.MAX_READS} support threads")
    assert "and 15 more" in item.detail and "5 more not checked yet" in item.detail   # 20 confirmed, 5 beyond the read cap


async def test_the_answer_times_come_from_the_profile_when_it_says_so(world, fake, sessions, owner, monkeypatch):
    runtime, agent = await ready(world, fake, sessions)
    monkeypatch.setattr(support_unanswered.business_profile, "read",
                        lambda: {"fields": {"hours": "Answer within 2 days. Urgent threads within 2 hours."}})
    await mark(sessions, owner, agent, fake, "u1", 3, "angry")                # 3h > 2h urgent
    await mark(sessions, owner, agent, fake, "q1", 30, "question")           # 30h < 48h: not overdue now
    (item,) = await collect(sessions, owner)
    assert "u1" in item.detail and "q1" not in item.detail and "2h for urgent, 48h for the rest" in item.detail
    assert support_windows.windows_from_profile("Answer within 2 days").normal_hours == 48.0


async def test_the_card_carries_the_item_once_and_caches_for_five_minutes(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    await mark(sessions, owner, agent, fake, "a1", 6, "angry")

    class Clock:
        now = NOW
        def __call__(self):
            return self.now
    clock = Clock()

    async def empty():
        return []
    ny = needs_you.NeedsYou(runtime, clock, engines=empty, issues=empty)
    card = await ny.read()
    rows = [i for i in card["items"] if i["kind"] == "support_unanswered"]
    assert len(rows) == 1 and rows[0]["title"].startswith("1 support thread") and rows[0]["source"].startswith("Support worker")
    assert SECRET not in json.dumps(card)
    reads = len(fake.executes)
    clock.now = NOW + timedelta(minutes=2)
    await ny.read()
    assert len(fake.executes) == reads                                        # cached: no new Gmail call
    fake.threads["a1"].append(message(ME, NOW, "answered", n=9))
    clock.now = NOW + timedelta(minutes=6)                                    # the cache has expired
    card = await ny.read()
    assert [i for i in card["items"] if i["kind"] == "support_unanswered"] == []


async def test_a_change_of_share_applies_at_once_even_inside_the_cache_window(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    thread = thread_in(rule(5), False)
    await mark(sessions, owner, agent, fake, thread, 6, "angry")

    async def empty():
        return []
    ny = needs_you.NeedsYou(runtime, lambda: NOW, engines=empty, issues=empty)
    assert len([i for i in (await ny.read())["items"] if i["kind"] == "support_unanswered"]) == 1
    await grant(sessions, agent, {"gmail": {ACCOUNT: entry(5)}})              # now this thread is outside the share
    assert [i for i in (await ny.read())["items"] if i["kind"] == "support_unanswered"] == []


def test_the_new_kind_ranks_just_after_routine_failures():
    order = needs_you.KIND_ORDER
    assert order["routine"] < order["support_unanswered"] < order["due"] < order["gmail_message"]
