"""Keep the model's prompt cache warm for chats that were just in use (BL-120).

Engines that sign in with a subscription cache a long conversation for only a
few minutes. A chat that goes quiet for longer pays full price and waits for
the whole context to be read again on the next message. When the person turns
this on, a small background job sends one very short message to the chats they
used recently, just often enough to stay inside that window.

Off by default. Strict limits keep it cheap and quiet:

- only the engines in `ENGINES`, and only normal chats;
- at most `max_threads` chats, the most recently used first;
- a chat is left alone once the person has not touched it for `idle_hours`;
- never while a turn is running in that chat;
- the gap between two pings is shorter than the cache lifetime and carries a
  small random spread, so chats do not all wake together;
- a ping that failed is not repeated before the next gap.

The ping is a real, tiny turn (the only way to refresh the provider's cache),
so it appears in the chat. It is sent in "plan" mode with no configuration
changes allowed. Its own time never counts as the person's activity, so a chat
stops being warmed `idle_hours` after the person last used it.

The scheduler is a plain class with an injectable clock, random source and
sender so its rules are tested without an engine.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)

# Engines that sign in with a subscription and cache the conversation briefly.
ENGINES = frozenset({"claude", "codex"})

# The shortest cache lifetime among those engines is about five minutes; stay well inside it.
CACHE_SECONDS = 300
PING_TEXT = "Cache keep-alive. Reply with the single word: ok"

# A change in a chat's last-updated time smaller than this after our own ping is ours, not the person's.
_SLACK_SECONDS = 2.0


@dataclass(frozen=True)
class Candidate:
    thread_id: int
    provider: str
    updated_at: float  # epoch seconds of the chat's last change


@dataclass
class _State:
    real_at: float  # the person's last activity
    marker: float | None = None  # the chat's updated_at right after our last ping
    absorb_next: bool = False  # our last ping did not report the new time: ignore the next change once
    last_ping: float = 0.0
    next_gap: float = 0.0


class KeepWarm:
    def __init__(
        self,
        *,
        enabled: Callable[[], bool],
        candidates: Callable[[float, int], Awaitable[list[Candidate]]],
        send: Callable[[int], Awaitable[float | None]],
        busy: Callable[[int], bool],
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        max_threads: int = 3,
        idle_hours: float = 2.0,
        interval_seconds: float = 240.0,
        engines: frozenset[str] = ENGINES,
    ) -> None:
        self._enabled = enabled
        self._candidates = candidates
        self._send = send
        self._busy = busy
        self._clock = clock
        self._rng = rng or random.Random()
        self.max_threads = max(1, int(max_threads))
        self.idle_seconds = max(0.0, float(idle_hours)) * 3600
        # Never longer than the cache lasts, with room for a slow tick.
        self.interval = min(max(30.0, float(interval_seconds)), CACHE_SECONDS * 0.9)
        self.engines = engines
        self._state: dict[int, _State] = {}

    def _gap(self) -> float:
        # 70% to 100% of the interval.
        return self.interval * self._rng.uniform(0.7, 1.0)

    def _real_activity(self, c: Candidate) -> float:
        state = self._state.get(c.thread_id)
        if state is None:
            return c.updated_at
        if state.marker is not None and c.updated_at <= state.marker + _SLACK_SECONDS:
            return state.real_at
        if state.absorb_next and c.updated_at > state.real_at:
            state.absorb_next = False
            return state.real_at
        return max(state.real_at, c.updated_at)

    async def tick(self) -> int:
        """Ping every chat that is due. Returns how many were pinged. Never raises."""
        try:
            if not self._enabled():
                self._state.clear()
                return 0
            now = self._clock()
            found = await self._candidates(now - self.idle_seconds - _SLACK_SECONDS, self.max_threads * 4)
        except Exception:  # noqa: BLE001
            logger.exception("keep-warm could not list chats")
            return 0
        eligible: list[tuple[Candidate, float]] = []
        for c in found:
            if c.provider not in self.engines:
                continue
            real = self._real_activity(c)
            state = self._state.get(c.thread_id)
            if state is None:
                self._state[c.thread_id] = _State(real_at=real, next_gap=self._gap())
            else:
                state.real_at = real
            if now - real > self.idle_seconds:
                continue
            eligible.append((c, real))
        eligible.sort(key=lambda item: item[1], reverse=True)
        sent = 0
        for c, real in eligible[: self.max_threads]:
            state = self._state[c.thread_id]
            last = max(real, state.last_ping)
            if now - last < state.next_gap:
                continue
            if self._busy(c.thread_id):
                continue
            state.last_ping = now
            state.next_gap = self._gap()
            try:
                marker = await self._send(c.thread_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("keep-warm ping failed", thread_id=c.thread_id, error=type(exc).__name__)
                continue
            state.marker = marker
            state.absorb_next = marker is None
            sent += 1
        # Forget chats the listing no longer returns (old, archived, deleted), so the table cannot
        # grow without bound. A chat that is only idle stays, so its own pings never look like the person.
        listed = {c.thread_id for c in found}
        for thread_id in [t for t in self._state if t not in listed]:
            del self._state[thread_id]
        return sent


def build(runtime) -> KeepWarm:
    """The scheduler wired to the real runtime and settings."""
    from app.core.config import settings

    from app.services import memory_settings

    async def candidates(since: float, limit: int) -> list[Candidate]:
        from datetime import datetime, timezone

        from sqlalchemy import select

        from app.db.models import Thread

        async with runtime.session() as db:
            rows = (
                await db.execute(
                    select(Thread.id, Thread.provider, Thread.updated_at)
                    .where(
                        Thread.owner_id == runtime.owner_id,
                        Thread.archived_at.is_(None),
                        Thread.kind == "chat",
                        Thread.provider.in_(sorted(ENGINES)),
                        Thread.session_state != "broken",
                        Thread.provider_session_id.is_not(None),
                        Thread.updated_at >= datetime.fromtimestamp(since, tz=timezone.utc),
                    )
                    .order_by(Thread.updated_at.desc())
                    .limit(limit)
                )
            ).all()
        out = []
        for thread_id, provider, updated in rows:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            out.append(Candidate(thread_id, provider, updated.timestamp()))
        return out

    async def send(thread_id: int) -> float | None:
        from datetime import timezone

        from sqlalchemy import select

        from app.db.models import Thread

        await runtime.send_turn(
            thread_id,
            PING_TEXT,
            approval_mode="plan",
            configuration_allowed=False,
            internal=True,
        )
        # Wait for the tiny turn to end so its time can be told apart from the person's.
        deadline = time.monotonic() + 120
        while runtime.is_running(thread_id) and time.monotonic() < deadline:
            await asyncio.sleep(1)
        async with runtime.session() as db:
            updated = (await db.execute(select(Thread.updated_at).where(Thread.id == thread_id))).scalar_one_or_none()
        if updated is None:
            return None
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return updated.timestamp()

    return KeepWarm(
        enabled=memory_settings.keep_cache_warm,
        candidates=candidates,
        send=send,
        busy=runtime.is_running,
        max_threads=settings.CACHE_KEEP_WARM_MAX_THREADS,
        idle_hours=settings.CACHE_KEEP_WARM_IDLE_HOURS,
        interval_seconds=settings.CACHE_KEEP_WARM_INTERVAL_SECONDS,
    )
