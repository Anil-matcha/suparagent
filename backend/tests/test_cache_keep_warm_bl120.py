import asyncio
import random

from app.services import cache_keep_warm as kw
from app.services.cache_keep_warm import Candidate, KeepWarm


class Env:
    def __init__(self, **options):
        self.now = 10_000.0
        self.on = True
        self.rows: dict[int, Candidate] = {}
        self.busy: set[int] = set()
        self.sent: list[int] = []
        self.fail: set[int] = set()
        self.report_marker = True
        self.warm = KeepWarm(
            enabled=lambda: self.on,
            candidates=self._candidates,
            send=self._send,
            busy=lambda t: t in self.busy,
            clock=lambda: self.now,
            rng=random.Random(1),
            **options,
        )

    async def _candidates(self, since, limit):
        rows = [c for c in self.rows.values() if c.updated_at >= since]
        return sorted(rows, key=lambda c: -c.updated_at)[:limit]

    async def _send(self, thread_id):
        if thread_id in self.fail:
            raise RuntimeError("engine down")
        self.sent.append(thread_id)
        old = self.rows[thread_id]
        self.rows[thread_id] = Candidate(thread_id, old.provider, self.now + 1)
        return self.now + 1 if self.report_marker else None

    def chat(self, thread_id, provider="claude", age=0.0):
        self.rows[thread_id] = Candidate(thread_id, provider, self.now - age)

    def tick(self):
        return asyncio.run(self.warm.tick())

    def advance(self, seconds):
        self.now += seconds


def test_off_by_default_sends_nothing():
    env = Env()
    env.on = False
    env.chat(1)
    env.advance(1000)
    assert env.tick() == 0 and env.sent == []


def test_pings_a_quiet_recent_chat_but_not_before_the_gap():
    env = Env(interval_seconds=240)
    env.chat(1)
    env.advance(60)
    assert env.tick() == 0
    env.advance(200)
    assert env.tick() == 1 and env.sent == [1]
    assert env.tick() == 0  # just pinged
    env.advance(240)
    assert env.tick() == 1 and env.sent == [1, 1]


def test_gap_is_always_under_the_cache_lifetime():
    assert KeepWarm(enabled=lambda: True, candidates=None, send=None, busy=None, interval_seconds=10_000).interval < kw.CACHE_SECONDS
    env = Env(interval_seconds=240)
    assert all(env.warm._gap() <= 240 for _ in range(100))


def test_only_subscription_engines_and_most_recent_max_threads():
    env = Env(max_threads=2)
    env.chat(1, age=30)
    env.chat(2, age=20)
    env.chat(3, age=10)
    env.chat(4, provider="openrouter", age=1)
    env.advance(300)
    assert env.tick() == 2
    assert sorted(env.sent) == [2, 3]


def test_never_pings_a_running_chat():
    env = Env()
    env.chat(1)
    env.busy.add(1)
    env.advance(300)
    assert env.tick() == 0
    env.busy.clear()
    assert env.tick() == 1


def test_stops_after_the_idle_window_and_our_own_pings_do_not_extend_it():
    env = Env(idle_hours=1, interval_seconds=240)
    env.chat(1)
    sent = 0
    for _ in range(40):  # 40 minutes of ticks
        env.advance(60)
        sent += env.tick()
    assert sent >= 8
    for _ in range(40):  # past the hour since the person last spoke
        env.advance(60)
        env.tick()
    before = len(env.sent)
    for _ in range(10):
        env.advance(60)
        env.tick()
    assert len(env.sent) == before
    assert env.now - 10_000 > 3600


def test_person_activity_restarts_the_window():
    env = Env(idle_hours=1, interval_seconds=240)
    env.chat(1)
    env.advance(3500)
    env.tick()
    env.advance(20)
    env.rows[1] = Candidate(1, "claude", env.now)  # the person writes again
    env.advance(3500)
    assert env.tick() == 1


def test_ping_time_ignored_even_when_the_sender_cannot_report_it():
    env = Env(idle_hours=1, interval_seconds=240)
    env.report_marker = False
    env.chat(1)
    env.advance(300)
    assert env.tick() == 1
    env.advance(3400)
    env.tick()
    env.advance(300)
    before = len(env.sent)
    env.tick()
    assert len(env.sent) == before  # more than an hour since the person, no more pings


def test_failed_ping_is_not_retried_before_the_next_gap_and_never_raises():
    env = Env(interval_seconds=240)
    env.chat(1)
    env.fail.add(1)
    env.advance(300)
    assert env.tick() == 0
    assert env.tick() == 0
    env.fail.clear()
    env.advance(240)
    assert env.tick() == 1


def test_listing_error_never_raises():
    env = Env()

    async def broken(since, limit):
        raise RuntimeError("db down")

    env.warm._candidates = broken
    assert env.tick() == 0


def test_setting_defaults_off_and_validates(tmp_path, monkeypatch):
    from app.services import memory_settings

    monkeypatch.setattr(memory_settings, "_path", lambda: tmp_path / "settings.json")
    assert memory_settings.keep_cache_warm() is False
    assert memory_settings.update(keep_cache_warm=True)["keep_cache_warm"] is True
    assert memory_settings.update(keep_cache_warm=False)["keep_cache_warm"] is False
    try:
        memory_settings.update(keep_cache_warm="yes")
    except memory_settings.InvalidSetting:
        pass
    else:
        raise AssertionError("expected InvalidSetting")
