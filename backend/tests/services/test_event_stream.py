from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services import event_stream
from app.services.relay_client import Frame, RelayError


def message_frame(seq=7):
    return Frame(seq, "composio", json.dumps({
        "event_id": "delivery-1",
        "trigger_id": "trigger_1",
        "connected_account_id": "account_1",
        "thread_id": "abcdef",
        "message_id": "123456",
        "received_at": "2026-11-26T12:30:00Z",
        "subject": "must be discarded",
    }))


def status_frame(seq=8):
    return Frame(seq, "composio", json.dumps({
        "event_id": "delivery-2", "kind": "status", "status": "trigger_disabled",
        "trigger_id": "trigger_1", "irrelevant": "discard",
    }))


def test_parse_message_and_status_frames_to_id_only_events():
    message = event_stream.parse_frame(message_frame())
    status = event_stream.parse_frame(status_frame())
    assert message["kind"] == "message"
    assert message["thread_id"] == "abcdef"
    assert message["message_id"] == "123456"
    assert message["received_at"].isoformat() == "2026-11-26T12:30:00+00:00"
    assert status["kind"] == "status"
    assert status["status"] == "trigger_disabled"
    assert "subject" not in message and "irrelevant" not in status


@pytest.mark.parametrize("frame", [
    Frame(1, "composio", "{"),
    Frame(1, "composio", "x" * (33 * 1024)),
    Frame(1, "other", "{}"),
    Frame(None, "composio", '{"id":"x"}'),
    Frame(1, "composio", json.dumps({"event_id": "x", "kind": "status", "status": "unknown"})),
])
def test_invalid_frames_are_dropped(frame):
    assert event_stream.parse_frame(frame) is None


class Session:
    def __init__(self, trace):
        self.trace = trace

    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_):
        return False


class Scheduler:
    owner_id = "owner"
    enabled = True

    def __init__(self, trace=None):
        self.trace = trace if trace is not None else []
        self.kicks = 0

    def session(self):
        return Session(self.trace)

    def kick(self):
        self.kicks += 1
        self.trace.append("kick")


class Relay:
    def __init__(self, frames=(), trace=None, error=None):
        self.frames, self.trace, self.error = frames, trace if trace is not None else [], error
        self.stream_cursors, self.acks = [], []

    def available(self):
        return True

    @asynccontextmanager
    async def stream(self, cursor):
        self.stream_cursors.append(cursor)
        if self.error:
            raise self.error
        async def iterate():
            for frame in self.frames:
                yield frame
        yield iterate()

    async def ack(self, seq):
        self.trace.append("ack")
        self.acks.append(seq)


@pytest.mark.asyncio
async def test_decision_commits_before_ack_and_cursor_saves_after_ack(monkeypatch):
    trace = []
    scheduler = Scheduler(trace)
    relay = Relay([message_frame()], trace)
    listener = event_stream.EventStream(scheduler, lambda _: object(), relay_provider=lambda: relay)

    async def accepted(db, route, event, *, owner_id):
        trace.append("decision-commit")
        assert event["kind"] == "message"
        return {"outcome": "queued", "run_id": 12}

    async def save(db, owner, route, seq):
        trace.append("cursor-save")

    monkeypatch.setattr(event_stream.routine_service, "accept_app_event", accepted)
    monkeypatch.setattr(event_stream.app_events, "save_stream_cursor", save)
    await listener._connected(relay, 4, "route_12345678901234567890")
    assert trace[:3] == ["decision-commit", "ack", "cursor-save"]
    assert trace[-1] == "kick"
    assert relay.acks == [7]


@pytest.mark.asyncio
async def test_status_commits_and_acks_without_kicking(monkeypatch):
    scheduler = Scheduler()
    relay = Relay([status_frame()])
    listener = event_stream.EventStream(scheduler, lambda _: object(), relay_provider=lambda: relay)
    seen = []
    pokes = []
    original_poke = listener.poke
    listener.poke = lambda: (pokes.append(True), original_poke())[1]

    async def accepted(db, route, event, *, owner_id):
        seen.append(event["kind"])
        return {"outcome": "status"}

    monkeypatch.setattr(event_stream.routine_service, "accept_app_event", accepted)
    monkeypatch.setattr(event_stream.app_events, "save_stream_cursor", lambda *args: asyncio.sleep(0))
    await listener._connected(relay, 0, "route_12345678901234567890")
    assert seen == ["status"]
    assert pokes == [True]
    assert scheduler.kicks == 0
    assert relay.acks == [8]


@pytest.mark.asyncio
async def test_resume_uses_supplied_committed_cursor():
    relay = Relay([])
    listener = event_stream.EventStream(Scheduler(), lambda _: None, relay_provider=lambda: relay)
    await listener._connected(relay, 31, "route_12345678901234567890")
    assert relay.stream_cursors == [31]


@pytest.mark.asyncio
async def test_poke_interrupts_a_live_stream():
    relay = Relay()
    scheduler = Scheduler()
    listener = event_stream.EventStream(scheduler, lambda _: None, relay_provider=lambda: relay)
    entered = asyncio.Event()

    @asynccontextmanager
    async def blocked_stream(cursor):
        async def iterate():
            entered.set()
            await asyncio.Event().wait()
            yield Frame(1, "composio", "{}")
        yield iterate()

    relay.stream = blocked_stream
    task = asyncio.create_task(listener._connected(relay, 0, "route"))
    await entered.wait()
    listener.poke()
    await asyncio.wait_for(task, 0.5)


@pytest.mark.asyncio
async def test_no_session_and_no_trigger_are_idle(monkeypatch):
    scheduler = Scheduler()
    relay = Relay()
    relay.available = lambda: False
    listener = event_stream.EventStream(scheduler, lambda _: None, relay_provider=lambda: relay)
    assert await listener._state() == (False, 0, None)

    relay.available = lambda: True
    async def no_triggers(db, owner):
        return False
    monkeypatch.setattr(event_stream.app_events, "relay_triggers_exist", no_triggers)
    assert await listener._state() == (False, 0, None)


@pytest.mark.asyncio
async def test_reconnect_backoff_is_bounded_and_route_loss_heals(monkeypatch):
    scheduler = Scheduler()
    relay = Relay()
    listener = event_stream.EventStream(scheduler, lambda _: None, relay_provider=lambda: relay,
                                        random_source=lambda: 0.5)
    reconnects = []

    async def state():
        return True, 2, "route"

    async def connected(_relay, _cursor, _route):
        raise RelayError(404, "no_route")

    async def reconcile(db, owner, factory):
        reconnects.append("reconcile")

    async def heal(db, owner, factory):
        reconnects.append("heal")
        return True

    monkeypatch.setattr(listener, "_state", state)
    monkeypatch.setattr(listener, "_connected", connected)
    monkeypatch.setattr(event_stream.app_events, "reconcile", reconcile)
    monkeypatch.setattr(event_stream.app_events, "heal_relay", heal)
    delays = []

    async def wait(delay):
        delays.append(delay)
        if len(delays) == 9:
            await listener.stop()

    listener.wait_for_wake = wait
    await listener.run()
    assert reconnects.count("heal") == len(delays)
    assert len(delays) == 9
    assert delays[0] == 1.0
    assert delays[-1] == event_stream.MAX_BACKOFF
    assert all(0 < delay <= event_stream.MAX_BACKOFF for delay in delays)


@pytest.mark.asyncio
async def test_oversized_or_malformed_valid_sequence_is_acked(monkeypatch):
    scheduler = Scheduler()
    relay = Relay([Frame(9, "composio", None)])
    listener = event_stream.EventStream(scheduler, lambda _: None, relay_provider=lambda: relay)
    saved = []
    async def save(db, owner, route, seq):
        saved.append(seq)
    monkeypatch.setattr(event_stream.app_events, "save_stream_cursor", save)
    await listener._connected(relay, 0, "route")
    assert relay.acks == [9]
    assert saved == [9]


@pytest.mark.asyncio
async def test_the_default_client_maker_takes_the_api_key_the_listener_passes(monkeypatch):
    """start() wires the real client maker: reconcile calls make_client(api_key), so it must accept one argument and
    reach composio_bridge.make_client (a zero-argument factory here raised TypeError on every pass, before any connection)."""
    from app.services import composio_bridge, routine_service

    made = []
    monkeypatch.setattr(composio_bridge, "make_client", lambda key: made.append(key) or "client")
    captured = {}

    class FakeStream:
        def __init__(self, scheduler, make_client):
            captured["make_client"] = make_client

        def poke(self):  # _run_global forwards a pending wake signal left by an earlier test
            pass

        async def run(self):
            return None

    monkeypatch.setattr(event_stream, "EventStream", FakeStream)
    monkeypatch.setattr(event_stream, "_task", None)
    task = event_stream.start(scheduler=routine_service.scheduler)
    await task
    assert captured["make_client"]("api-key-1") == "client"
    assert made == ["api-key-1"]
    monkeypatch.setattr(event_stream, "_task", None)
