"""Failed-attempt lockout for the sign-in and pairing endpoints.

Failed-attempt lockouts are written to a small file when one is configured
(`configure_persistence`), so a restart does not forget them; several worker
processes still each keep their own count (docs/AUTH.md, "Limits"). That is a slow-down for
online guessing, not the only defence: pairing codes also die after a few wrong
guesses in total, and the passphrase hash is slow.
"""

import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_TRACKED_SOURCES = 10_000


@dataclass
class _Entry:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class Lockout:
    def __init__(
        self,
        *,
        max_failures: int,
        window_seconds: float,
        lock_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lock_seconds = lock_seconds
        self.clock = clock
        self.wall_clock = wall_clock
        self._entries: dict[str, _Entry] = {}
        self._path: Path | None = None

    # -- persistence ------------------------------------------------------
    def attach(self, path: Path | None) -> None:
        """Keep state in `path` (None: memory only) and load what it holds."""
        self._path = path
        if path is None:
            return
        try:
            raw = json.loads(path.read_text())
            now, wall = self.clock(), self.wall_clock()
            for source, data in raw.items():
                locked = float(data.get("locked_until", 0)) - wall
                failures = [now - (wall - float(t)) for t in data.get("failures", [])]
                failures = [t for t in failures if now - t < self.window_seconds]
                if locked > 0 or failures:
                    self._entries[source] = _Entry(failures=failures, locked_until=now + locked if locked > 0 else 0.0)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError, TypeError):
            logger.warning("Ignoring unreadable sign-in limit file %s", path)

    def _save(self) -> None:
        if self._path is None:
            return
        now, wall = self.clock(), self.wall_clock()
        live = {}
        for source, e in self._entries.items():
            failures = [wall - (now - t) for t in e.failures if now - t < self.window_seconds]
            locked = wall + (e.locked_until - now) if e.locked_until > now else 0.0
            if failures or locked:
                live[source] = {"failures": failures, "locked_until": locked}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".limits.", dir=self._path.parent)
            with os.fdopen(fd, "w") as handle:
                json.dump(live, handle)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except OSError:
            logger.warning("Could not save sign-in limit file %s", self._path)

    def retry_after(self, source: str) -> int:
        """Seconds until `source` may try again; 0 when it may now."""
        entry = self._entries.get(source)
        if entry is None:
            return 0
        remaining = entry.locked_until - self.clock()
        return int(remaining) + 1 if remaining > 0 else 0

    def failure(self, source: str) -> None:
        now = self.clock()
        if len(self._entries) >= MAX_TRACKED_SOURCES and source not in self._entries:
            self._prune(now)
        entry = self._entries.setdefault(source, _Entry())
        entry.failures = [t for t in entry.failures if now - t < self.window_seconds]
        entry.failures.append(now)
        if len(entry.failures) >= self.max_failures:
            entry.locked_until = now + self.lock_seconds
            entry.failures.clear()
        self._save()

    def success(self, source: str) -> None:
        if self._entries.pop(source, None) is not None:
            self._save()

    def reset(self) -> None:
        self._entries.clear()
        self._save()

    def _prune(self, now: float) -> None:
        for key in [k for k, e in self._entries.items()
                    if e.locked_until <= now and all(now - t >= self.window_seconds for t in e.failures)]:
            del self._entries[key]
        if len(self._entries) >= MAX_TRACKED_SOURCES:
            # Still full of live entries: forget the oldest rather than grow.
            for key in list(self._entries)[: len(self._entries) // 2]:
                del self._entries[key]


class RateLimit:
    """At most `limit` events per `window_seconds` per source, successes included."""

    def __init__(self, *, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.clock = clock
        self._events: dict[str, list[float]] = {}

    def allow(self, source: str) -> bool:
        now = self.clock()
        if len(self._events) >= MAX_TRACKED_SOURCES and source not in self._events:
            self._events.clear()
        events = [t for t in self._events.get(source, []) if now - t < self.window_seconds]
        if len(events) >= self.limit:
            self._events[source] = events
            return False
        events.append(now)
        self._events[source] = events
        return True

    def reset(self) -> None:
        self._events.clear()


# Five wrong passphrases from one address lock it for fifteen minutes.
login_lockout = Lockout(max_failures=5, window_seconds=15 * 60, lock_seconds=15 * 60)
# Pairing codes are random, so this only has to slow a guesser down.
pair_lockout = Lockout(max_failures=10, window_seconds=10 * 60, lock_seconds=10 * 60)
# Any request to an auth endpoint that does work (scrypt, a code lookup).
auth_requests = RateLimit(limit=30, window_seconds=60)


def configure_persistence(directory: Path | None) -> None:
    """Store the two lockouts as files in `directory`; None keeps them in memory."""
    login_lockout.attach(None if directory is None else directory / "auth-login-limits.json")
    pair_lockout.attach(None if directory is None else directory / "auth-pair-limits.json")


def reset_all() -> None:
    login_lockout.reset()
    pair_lockout.reset()
    auth_requests.reset()
