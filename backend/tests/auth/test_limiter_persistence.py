from datetime import timedelta

import pytest

from app.auth import limiter, store
from app.auth.limiter import Lockout
from app.core.config import Settings


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(path, mono, wall):
    lock = Lockout(max_failures=3, window_seconds=60, lock_seconds=300, clock=mono, wall_clock=wall)
    lock.attach(path)
    return lock


def test_lockout_survives_restart(tmp_path):
    mono, wall = Clock(), Clock()
    path = tmp_path / "limits.json"
    first = make(path, mono, wall)
    for _ in range(3):
        first.failure("1.2.3.4")
    assert first.retry_after("1.2.3.4") > 0

    # New process: monotonic clock restarts from an unrelated value.
    mono2, wall2 = Clock(), Clock()
    mono2.t, wall2.t = 5.0, wall.t + 100
    second = make(path, mono2, wall2)
    assert 190 <= second.retry_after("1.2.3.4") <= 201

    wall2.t += 400
    mono2.t += 400
    assert make(path, mono2, wall2).retry_after("1.2.3.4") == 0


def test_partial_failures_persist_and_success_clears(tmp_path):
    mono, wall = Clock(), Clock()
    path = tmp_path / "limits.json"
    first = make(path, mono, wall)
    first.failure("a")
    first.failure("a")
    second = make(path, mono, wall)
    second.failure("a")
    assert second.retry_after("a") > 0
    second.success("a")
    assert make(path, mono, wall).retry_after("a") == 0


def test_unreadable_file_is_ignored(tmp_path):
    path = tmp_path / "limits.json"
    path.write_text("not json")
    assert make(path, Clock(), Clock()).retry_after("x") == 0


def test_configure_persistence_writes_files(tmp_path):
    try:
        limiter.configure_persistence(tmp_path)
        limiter.login_lockout.failure("z")
        assert (tmp_path / "auth-login-limits.json").exists()
    finally:
        limiter.configure_persistence(None)
        limiter.reset_all()


def test_session_length_default_and_bounds():
    assert store.SESSION_TTL == timedelta(days=30)
    assert store.SESSION_RENEW_WHEN_LEFT == timedelta(days=15)
    assert Settings(AUTH_SESSION_DAYS=7).AUTH_SESSION_DAYS == 7
    for bad in (0, 91):
        with pytest.raises(ValueError):
            Settings(AUTH_SESSION_DAYS=bad)
