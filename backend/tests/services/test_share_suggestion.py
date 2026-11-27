"""The share suggestion (BL-175): proposals only, every threshold boundary, and it never writes the share.

The rule is pure (`decide`) and is tested on its numbers; `build` is tested through the real runtime, the real Gmail path and
the share rule, with a Gmail fake that serves the threads each test describes."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import gmail_share
from app.db.models import Agent, ShadowFeedback
from app.services import share_suggestion as s
from tests.services.test_composio_bridge import world  # noqa: F401
from tests.services.test_composio_gmail_share import ACCOUNT, entry, rule
from tests.services.test_shadow_report import CUSTOMER, ME, NOW, fake, message, predict, ready  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401

SAME = "thank you for writing in we have fixed the problem on our side today"
OTHER = "completely unrelated words about something else entirely here now"


def tally(evaluated, usable):
    return {"evaluated": evaluated, "usable": usable, "no_reply_seen": 0}


NONE = tally(0, 0)


# -- the pure rule -----------------------------------------------------------------------------------------------------------

def test_wilson_known_values():
    assert s.wilson(0, 0) is None
    low, high = s.wilson(17, 20)
    assert (round(low, 3), round(high, 3)) == (0.640, 0.948)
    assert round(s.wilson(16, 20)[0], 3) == 0.584 and round(s.wilson(15, 20)[0], 3) == 0.531
    assert round(s.wilson(4, 15)[1], 3) == 0.520 and round(s.wilson(5, 15)[1], 3) == 0.583
    assert round(s.wilson(30, 40)[0], 4) == 0.5981  # just under 0.60, so 30 of 40 does NOT pass the raise rule
    assert round(s.wilson(31, 40)[0], 3) == 0.625
    low, high = s.wilson(20, 20)
    assert high > 0.999 and 0.83 < low < 0.84
    assert s.wilson(0, 15)[0] == 0.0


def test_the_ladder_steps():
    assert [s.step_up(p) for p in (1, 5, 10, 15, 25, 50, 99, 100)] == [5, 10, 25, 25, 50, 100, 100, None]
    assert [s.step_down(p) for p in (100, 50, 30, 25, 10, 5, 3)] == [50, 25, 25, 10, 5, None, None]


def raised(n, usable, **kw):
    return s.decide(kw.pop("current", 10), tally(n, usable), kw.pop("before", NONE), kw.pop("days", 28), kw.pop("wrong_policy", 0))


def test_seventeen_of_twenty_suggests_raise():
    answer = raised(20, 17)
    assert answer["state"] == "raise" and answer["suggested_percent"] == 25
    assert answer["evidence"] == {"evaluated": 20, "usable": 17, "usable_rate": 0.85, "lower": 0.64, "upper": 0.948, "days": 28, "wrong_policy": 0}
    assert "17 of 20 drafts" in answer["reason"] and "10% to 25%" in answer["reason"] and "64% to 95%" in answer["reason"]


@pytest.mark.parametrize("n,usable", [(20, 16), (20, 15), (40, 30)])
def test_below_the_lower_bound_does_not_raise(n, usable):
    answer = raised(n, usable)
    assert answer["state"] == "none" and answer["suggested_percent"] is None and "Stay at 10%" in answer["reason"]


def test_thirty_one_of_forty_raises():
    assert raised(40, 31)["state"] == "raise"


@pytest.mark.parametrize("n", [0, 5, 19])
def test_under_twenty_drafts_says_not_enough_evidence(n):
    answer = raised(n, n)
    assert answer["state"] == "none" and "Not enough evidence yet" in answer["reason"] and f"{20 - n} more drafts" in answer["reason"]


def test_exactly_twenty_is_enough_and_thirteen_days_is_not():
    assert raised(20, 20)["state"] == "raise"
    answer = raised(20, 20, days=13)
    assert answer["state"] == "none" and "14 days" in answer["reason"]
    assert raised(20, 20, days=14)["state"] == "raise"


def test_wrong_policy_feedback_blocks_a_raise():
    answer = raised(20, 20, wrong_policy=1)
    assert answer["state"] == "none" and "wrong policy" in answer["reason"]


def test_a_full_share_has_nothing_to_raise():
    answer = raised(20, 20, current=100)
    assert answer["state"] == "none" and answer["reason"] == "It already sees all of your mail."


def test_four_of_fifteen_suggests_lower_and_five_of_fifteen_does_not():
    answer = raised(15, 4, current=25)
    assert answer["state"] == "lower" and answer["suggested_percent"] == 10 and "25% to 10%" in answer["reason"]
    assert raised(15, 5, current=25)["state"] == "none"
    assert raised(14, 0, current=25)["state"] == "none"  # under fifteen drafts there is not enough to lower on


def test_a_drop_of_twenty_points_suggests_lower():
    answer = raised(16, 11, current=25, before=tally(16, 15))  # 94% down to 69%
    assert answer["state"] == "lower" and answer["suggested_percent"] == 10 and "fell from 94% to 69%" in answer["reason"]
    assert raised(20, 15, current=25, before=tally(20, 19))["state"] == "lower"  # exactly 20 points
    assert raised(20, 16, current=25, before=tally(20, 20))["state"] == "lower"
    assert raised(20, 16, current=25, before=tally(20, 19))["state"] != "lower"   # 15 points is not enough
    assert raised(16, 11, current=25, before=tally(14, 14))["state"] != "lower"  # the earlier window needs fifteen drafts too


def test_lowering_wins_over_raising():
    answer = raised(100, 80, current=25, before=tally(100, 100))  # a high rate that slipped 20 points
    assert answer["state"] == "lower"


def test_the_bottom_rung_cannot_be_lowered():
    answer = raised(15, 2, current=5)
    assert answer["state"] == "none" and answer["suggested_percent"] is None


def test_a_custom_percent_moves_to_the_next_rung():
    assert raised(20, 20, current=15)["suggested_percent"] == 25
    assert raised(15, 3, current=30)["suggested_percent"] == 25


# -- through the runtime ------------------------------------------------------------------------------------------------------

def threads_in(percent, count, start=0):
    r = rule(percent)
    found, n = [], start
    while len(found) < count:
        if gmail_share.in_share(r, f"th{n}"):
            found.append(f"th{n}")
        n += 1
    return found


async def seed(sessions, owner, agent, fake, plan, *, first_day=1):
    """plan = [(thread, kind)] with kind 'same', 'other' or 'none'; thread i is predicted i/2 days after `first_day` days ago."""
    for i, (thread, kind) in enumerate(plan):
        at = NOW - timedelta(days=first_day, hours=i)
        await predict(sessions, owner, agent, thread, i + 1, at, draft=SAME, turn=1000 + i)
        reply = {"same": SAME, "other": OTHER}.get(kind)
        fake.threads[thread] = [message(CUSTOMER, at - timedelta(hours=1), n=1)] + ([message(ME, at + timedelta(hours=2), reply, n=2)] if reply else [])


def spread(sessions_plan, days_back):
    return sessions_plan


async def seeded(world, fake, sessions, owner, *, usable, rewritten, percent=10, oldest_days=20):
    runtime, agent = await ready(world, fake, sessions, {"gmail": {ACCOUNT: entry(percent)}})
    threads = threads_in(percent, usable + rewritten)
    plan = [(t, "same" if i < usable else "other") for i, t in enumerate(threads)]
    for i, (thread, kind) in enumerate(plan):
        at = NOW - timedelta(days=oldest_days) + timedelta(hours=i * (oldest_days * 24 - 30) / max(1, len(plan) - 1))
        await predict(sessions, owner, agent, thread, i + 1, at, draft=SAME, turn=1000 + i)
        fake.threads[thread] = [message(CUSTOMER, at - timedelta(hours=1), n=1), message(ME, at + timedelta(hours=2), SAME if kind == "same" else OTHER, n=2)]
    return runtime, agent


async def test_seventeen_of_twenty_through_the_whole_path(world, fake, sessions, owner):
    runtime, agent = await seeded(world, fake, sessions, owner, usable=17, rewritten=3)
    result = await s.build(runtime, agent.id, now=NOW)
    assert result["state"] == "raise" and result["current_percent"] == 10 and result["suggested_percent"] == 25
    assert result["account_id"] == ACCOUNT and result["evidence"]["evaluated"] == 20 and result["evidence"]["usable"] == 17
    assert result["evidence"]["days"] >= 14


async def test_sixteen_of_twenty_does_not(world, fake, sessions, owner):
    runtime, agent = await seeded(world, fake, sessions, owner, usable=16, rewritten=4)
    result = await s.build(runtime, agent.id, now=NOW)
    assert result["state"] == "none" and "Stay at 10%" in result["reason"]


async def test_a_young_result_set_waits_for_fourteen_days(world, fake, sessions, owner):
    runtime, agent = await seeded(world, fake, sessions, owner, usable=20, rewritten=0, oldest_days=9)
    result = await s.build(runtime, agent.id, now=NOW)
    assert result["state"] == "none" and "14 days" in result["reason"] and result["evidence"]["days"] <= 9


async def test_wrong_policy_feedback_in_the_window_blocks_the_raise(world, fake, sessions, owner):
    runtime, agent = await seeded(world, fake, sessions, owner, usable=20, rewritten=0)
    thread = threads_in(10, 1)[0]
    async with sessions() as db:
        db.add(ShadowFeedback(owner_id=owner, agent_id=agent.id, account_id=ACCOUNT, thread_id=thread, kind="draft",
                              reason="wrong_policy", created_at=NOW - timedelta(days=3)))
        await db.commit()
    result = await s.build(runtime, agent.id, now=NOW)
    assert result["state"] == "none" and result["evidence"]["wrong_policy"] == 1
    async with sessions() as db:  # an old one, outside the 28 day window, does not count
        row = (await db.execute(select(ShadowFeedback))).scalars().one()
        row.created_at = NOW - timedelta(days=40)
        await db.commit()
    s._CACHE.clear()
    assert (await s.build(runtime, agent.id, now=NOW))["state"] == "raise"


async def test_four_of_fifteen_lowers_to_the_next_rung(world, fake, sessions, owner):
    runtime, agent = await seeded(world, fake, sessions, owner, usable=4, rewritten=11, percent=25)
    result = await s.build(runtime, agent.id, now=NOW)
    assert result["state"] == "lower" and result["current_percent"] == 25 and result["suggested_percent"] == 10


async def test_a_drop_against_the_previous_four_weeks_lowers(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions, {"gmail": {ACCOUNT: entry(25)}})
    threads = threads_in(25, 32)
    now_threads, before_threads = threads[:16], threads[16:]
    for i, thread in enumerate(now_threads):  # this window: 11 of 16 usable
        at = NOW - timedelta(days=3 + i)
        await predict(sessions, owner, agent, thread, i + 1, at, draft=SAME, turn=10 + i)
        fake.threads[thread] = [message(ME, at + timedelta(hours=2), SAME if i < 11 else OTHER, n=1)]
    for i, thread in enumerate(before_threads):  # the 28 days before: 15 of 16 usable
        at = NOW - timedelta(days=30 + i)
        await predict(sessions, owner, agent, thread, i + 1, at, draft=SAME, turn=100 + i)
        fake.threads[thread] = [message(ME, at + timedelta(hours=2), SAME if i < 15 else OTHER, n=1)]
    result = await s.build(runtime, agent.id, now=NOW)
    assert result["state"] == "lower" and result["suggested_percent"] == 10 and result["evidence"]["evaluated"] == 16
    assert "fell from 94% to 69%" in result["reason"]


async def test_older_predictions_are_not_in_the_current_window(world, fake, sessions, owner):
    runtime, agent = await seeded(world, fake, sessions, owner, usable=20, rewritten=0, oldest_days=20)
    extra = threads_in(10, 40)[20:]
    for i, thread in enumerate(extra):  # forty to sixty days old: only the previous window
        at = NOW - timedelta(days=40 + i // 2)
        await predict(sessions, owner, agent, thread, 1, at, draft=SAME, turn=500 + i)
        fake.threads[thread] = [message(ME, at + timedelta(hours=2), OTHER, n=1)]
    result = await s.build(runtime, agent.id, now=NOW)
    assert result["evidence"]["evaluated"] == 20 and result["state"] == "raise"


async def test_computing_a_suggestion_never_changes_the_share(world, fake, sessions, owner):
    runtime, agent = await seeded(world, fake, sessions, owner, usable=20, rewritten=0)

    async def stored():
        async with sessions() as db:
            row = await db.get(Agent, agent.id, populate_existing=True)
            return json.dumps(row.connector_shares, sort_keys=True), row.updated_at

    before = await stored()
    for refresh in (False, True, False):
        result = await s.build(runtime, agent.id, now=NOW, refresh=refresh)
        assert result["state"] == "raise"
    assert await stored() == before  # byte-identical, and the agent row was not touched


async def test_a_full_mailbox_and_a_missing_account_are_explained(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions, {})
    result = await s.build(runtime, agent.id, now=NOW)
    assert result["state"] == "none" and result["current_percent"] == 100 and result["reason"] == "It already sees all of your mail."
    from app.services import agent_store
    async with sessions() as db:
        await agent_store.update_agent(db, agent.id, connector_scopes={}, connector_account_scopes={})
    s._CACHE.clear()
    result = await s.build(runtime, agent.id, now=NOW)
    assert result["state"] == "none" and result["account_id"] is None and "Gmail access" in result["reason"]


async def test_a_damaged_share_suggests_nothing(world, fake, sessions, owner):
    runtime, agent = await seeded(world, fake, sessions, owner, usable=20, rewritten=0)
    async with sessions() as db:
        (await db.get(Agent, agent.id)).connector_shares = {"gmail": {ACCOUNT: {"percent": 0}}}
        await db.commit()
    result = await s.build(runtime, agent.id, now=NOW)
    assert result["state"] == "none" and "not set up correctly" in result["reason"]


async def test_another_owners_agent_gets_nothing(world, fake, sessions, owner):
    from uuid import uuid4

    from app.services import agent_store
    runtime, agent = await ready(world, fake, sessions, {})
    async with sessions() as db:
        theirs = await agent_store.create_agent(db, owner_id="someone-else-" + uuid4().hex, name="X")
    assert await s.build(runtime, theirs.id, now=NOW) is None
    assert await s.build(runtime, 987654321, now=NOW) is None


async def test_the_mailbox_reads_are_kept_for_ten_minutes_and_refresh_is_limited(world, fake, sessions, owner):
    runtime, agent = await seeded(world, fake, sessions, owner, usable=17, rewritten=3)
    await s.build(runtime, agent.id, now=NOW)
    reads = len(fake.executes)
    assert reads > 0
    await s.build(runtime, agent.id, now=NOW)
    assert len(fake.executes) == reads  # served from the kept counts
    await s.build(runtime, agent.id, now=NOW, refresh=True)
    after_refresh = len(fake.executes)
    assert after_refresh > reads
    await s.build(runtime, agent.id, now=NOW, refresh=True)
    assert len(fake.executes) == after_refresh  # a second refresh inside a minute is served from the kept counts


@pytest.fixture(autouse=True)
def clean_caches():
    s._CACHE.clear()
    s._REFRESHED.clear()
    from app.services import shadow_report
    shadow_report._CACHE.clear()
    shadow_report._REFRESHED.clear()
    yield
