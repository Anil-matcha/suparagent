"""One desktop listener for the account service's Composio event relay (BL-177)."""
from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable
from typing import Any

from app import composio_events
from app.services import app_events, relay_client, routine_service

MAX_BACKOFF = 60.0

# The process owns exactly one listener. poke() interrupts an idle stream/backoff so current state is re-read.
_task: asyncio.Task | None = None
_wakeup: asyncio.Event | None = None
_listener: "EventStream | None" = None


def _wake_event() -> asyncio.Event:
    global _wakeup
    if _wakeup is None:
        _wakeup = asyncio.Event()
    return _wakeup


def poke() -> None:
    """Ask the listener to re-check sign-in and trigger state promptly."""
    if _listener is not None:
        _listener.poke()
        return
    try:
        _wake_event().set()
    except RuntimeError:  # no running event loop yet
        pass


def _minimal_envelope(frame: relay_client.Frame) -> tuple[dict[str, Any] | None, str | None]:
    """Translate the relay's flat id-only frame into the Composio shape extract() validates."""
    if frame.data is None or frame.event != "composio":
        return None, None
    try:
        raw = json.loads(frame.data)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None, None
    if not isinstance(raw, dict):
        return None, None

    event_id = raw.get("event_id")
    if not isinstance(event_id, str):
        return None, None
    status_type = {
        "trigger_disabled": "composio.trigger.disabled",
        "account_expired": "composio.connected_account.expired",
    }
    if raw.get("kind") == "status":
        kind = status_type.get(raw.get("status"))
    elif raw.get("kind") in (None, "message"):
        kind = "composio.trigger.message"
    else:
        kind = None
    if kind is None:
        return None, None

    # Relay frames are already reduced, but rebuilding the envelope drops any unexpected fields defensively.
    metadata: dict[str, Any] = {}
    for key in ("trigger_id", "connected_account_id"):
        if isinstance(raw.get(key), str):
            metadata[key] = raw[key]
    payload = {
        key: raw[key]
        for key in ("thread_id", "message_id")
        if isinstance(raw.get(key), str)
    }
    envelope: dict[str, Any] = {"id": event_id, "type": kind, "metadata": metadata, "data": payload}
    timestamp = raw.get("received_at", raw.get("timestamp"))
    if isinstance(timestamp, str):
        envelope["timestamp"] = timestamp
    return envelope, event_id


def parse_frame(frame: relay_client.Frame) -> dict[str, Any] | None:
    """Validate a relay frame with the same id-only parser used by the direct webhook path."""
    if type(frame.id) is not int or frame.id < 1:
        return None
    envelope, event_id = _minimal_envelope(frame)
    if envelope is None:
        return None
    return composio_events.extract(envelope, event_id)


class EventStream:
    """Dependency-injectable loop; production wiring is provided by start()."""

    def __init__(
        self,
        scheduler,
        make_client: Callable,
        *,
        relay_provider=relay_client.get_client,
        random_source: Callable[[], float] = random.random,
        wait_for_wake: Callable[[float], Any] | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.make_client = make_client
        self.relay_provider = relay_provider
        self.random_source = random_source
        self.wait_for_wake = wait_for_wake
        self.wakeup = asyncio.Event()
        self.stopping = False

    def poke(self) -> None:
        self.wakeup.set()

    async def stop(self) -> None:
        self.stopping = True
        self.wakeup.set()

    async def _wait(self, delay: float) -> None:
        if self.wait_for_wake is not None:
            await self.wait_for_wake(delay)
            return
        try:
            await asyncio.wait_for(self.wakeup.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return
        finally:
            self.wakeup.clear()

    async def _state(self) -> tuple[bool, int, str | None]:
        relay = self.relay_provider()
        if not relay.available():
            return False, 0, None
        owner_id = self.scheduler.owner_id
        async with self.scheduler.session() as db:
            if not await app_events.relay_triggers_exist(db, owner_id):
                return False, 0, None
            cursor = await app_events.stream_cursor(db, owner_id)
            hook = await app_events.relay_hook_for(db, owner_id)
            route_id = hook.get("route_id") if hook else None
        return bool(route_id), cursor, route_id

    async def run(self) -> None:
        backoff = 1.0
        relay = self.relay_provider()
        while not self.stopping:
            try:
                should_connect, cursor, route_id = await self._state()
                if not should_connect:
                    backoff = 1.0
                    await self._wait(60.0)
                    continue
                async with self.scheduler.session() as db:
                    await app_events.reconcile(db, self.scheduler.owner_id, self.make_client)
                # Re-evaluate after reconciliation, which may remove/replace the route or triggers.
                should_connect, cursor, route_id = await self._state()
                if not should_connect or self.stopping:
                    continue
                await self._connected(relay, cursor, route_id)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if isinstance(exc, relay_client.RelayError) and exc.code == "no_route":
                    try:
                        async with self.scheduler.session() as db:
                            await app_events.heal_relay(db, self.scheduler.owner_id, self.make_client)
                    except Exception:
                        pass
                # Never log remote details, credentials, identifiers or payloads.
            if self.stopping:
                break
            delay = min(MAX_BACKOFF, backoff) * (0.75 + 0.5 * self.random_source())
            await self._wait(delay)
            backoff = min(MAX_BACKOFF, max(1.0, backoff * 2))

    async def _connected(self, relay, cursor: int, route_id: str) -> None:
        async with relay.stream(cursor) as frames:
            while not self.stopping:
                next_frame = asyncio.create_task(frames.__anext__())
                changed = asyncio.create_task(self.wakeup.wait())
                done, _ = await asyncio.wait((next_frame, changed), return_when=asyncio.FIRST_COMPLETED)
                if changed in done and self.wakeup.is_set():
                    next_frame.cancel()
                    await asyncio.gather(next_frame, return_exceptions=True)
                    self.wakeup.clear()
                    return
                changed.cancel()
                await asyncio.gather(changed, return_exceptions=True)
                if next_frame not in done:
                    return
                try:
                    frame = next_frame.result()
                except StopAsyncIteration:
                    return
                seq = frame.id
                if type(seq) is not int or seq < 1:
                    continue
                event = parse_frame(frame)
                outcome = None
                if event is not None:
                    async with self.scheduler.session() as db:
                        result = await routine_service.accept_app_event(db, route_id, event, owner_id=self.scheduler.owner_id)
                    outcome = result.get("outcome")
                # Invalid, malformed and over-limit frames with a usable sequence are deliberately consumed to avoid
                # poison loops. Valid decisions are committed before this acknowledgement.
                await relay.ack(seq)
                async with self.scheduler.session() as db:
                    await app_events.save_stream_cursor(db, self.scheduler.owner_id, route_id, seq)
                if outcome in ("queued", "merged") and self.scheduler.enabled:
                    self.scheduler.kick()
                elif outcome == "status":
                    # Status processing may have removed the final active trigger. Re-check after acknowledgement.
                    self.poke()


async def _run_global(scheduler, make_client) -> None:
    global _listener
    listener = EventStream(scheduler, make_client)
    _listener = listener
    # Transfer any poke that arrived before the task started.
    if _wake_event().is_set():
        listener.poke()
        _wake_event().clear()
    try:
        await listener.run()
    finally:
        _listener = None


def start(scheduler=None, make_client=None) -> asyncio.Task:
    """Start the singleton listener, usually during desktop app startup."""
    global _task
    if _task is not None and not _task.done():
        return _task
    if scheduler is None:
        scheduler = routine_service.scheduler
    if make_client is None:
        from app.routers.routines import _composio_factory
        make_client = _composio_factory
    _task = asyncio.get_running_loop().create_task(_run_global(scheduler, make_client), name="desktop-event-stream")
    return _task


async def stop() -> None:
    """Stop the singleton listener and wait for its stream context to close."""
    global _task
    poke()
    task = _task
    if task is not None:
        # The global runner's EventStream has its own wakeup; cancellation promptly closes the SSE context.
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    _task = None
