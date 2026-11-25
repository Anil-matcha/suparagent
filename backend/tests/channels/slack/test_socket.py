"""The Socket Mode client against a real WebSocket server on localhost that
plays Slack. No network beyond 127.0.0.1."""

import asyncio
import json

import pytest
import websockets
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve

from app.channels.slack.api import SlackAuthError, SlackError
from app.channels.slack.socket import SocketModeClient, check_socket_url
from tests.services.thread_harness import wait_until

SLACK_URL = "wss://wss-primary.slack.com/link/?ticket=one-time"


class Conn:
    def __init__(self, ws):
        self.ws = ws
        self.received: list[dict] = []

    async def send(self, **message):
        await self.ws.send(json.dumps(message))

    async def read(self):
        try:
            async for raw in self.ws:
                self.received.append(json.loads(raw))
        except websockets.exceptions.ConnectionClosed:
            pass


class FakeSlack:
    """A local server; `scripts[i]` plays connection i (the last repeats)."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.connections: list[Conn] = []
        self.server = None

    async def start(self):
        async def handler(ws):
            conn = Conn(ws)
            index = len(self.connections)
            self.connections.append(conn)
            reader = asyncio.create_task(conn.read())
            try:
                await self.scripts[min(index, len(self.scripts) - 1)](conn)
            except websockets.exceptions.ConnectionClosed:
                pass
            finally:
                reader.cancel()

        self.server = await serve(handler, "127.0.0.1", 0)
        self.url = "ws://127.0.0.1:%d" % self.server.sockets[0].getsockname()[1]

    async def close(self):
        self.server.close()
        await self.server.wait_closed()


def envelope(n, event_id=None):
    return {
        "type": "events_api", "envelope_id": f"e{n}", "accepts_response_payload": False,
        "payload": {"event_id": event_id or f"Ev{n}", "team_id": "T1",
                    "event": {"type": "message", "channel_type": "im", "user": "U1", "channel": "D1", "text": f"m{n}"}},
    }


async def hold(conn):
    await conn.send(type="hello")
    await conn.ws.wait_closed()


async def hello_then(*messages):
    async def script(conn):
        await conn.send(type="hello")
        for message in messages:
            await conn.send(**message)
        await conn.ws.wait_closed()

    return script


async def drop_without_hello(conn):
    await conn.ws.close(code=1011)


async def hello_then_drop(conn):
    await conn.send(type="hello")
    await asyncio.sleep(0.05)
    await conn.ws.close(code=1011)


class FakeApi:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    async def open_connection(self):
        self.calls += 1
        item = self.results.pop(0) if self.results else SLACK_URL
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
async def slack_factory():
    made = []

    async def make(*scripts):
        fake = FakeSlack(list(scripts))
        await fake.start()
        made.append(fake)
        return fake

    yield make
    for fake in made:
        await fake.close()


class Harness:
    def __init__(self, fake, api, handler, **options):
        self.sleeps: list[float] = []
        self.statuses: list[tuple[str, str | None]] = []

        async def sleep(seconds):
            self.sleeps.append(seconds)
            await asyncio.sleep(0.01)

        self.client = SocketModeClient(
            api, handler,
            on_status=lambda state, detail: self.statuses.append((state, detail)),
            connect=lambda url: ws_connect(fake.url),
            sleep=sleep, jitter=lambda: 0.0,
            stable_after=options.get("stable_after", 100.0),
            handler_timeout=options.get("handler_timeout", 5.0),
        )

    def states(self):
        return [state for state, _ in self.statuses]


async def started(fake, api, handler, **options):
    harness = Harness(fake, api, handler, **options)
    harness.client.start()
    return harness


async def test_hello_connects_and_an_envelope_is_acknowledged_then_handled(slack_factory):
    fake = await slack_factory(await hello_then(envelope(1)))
    seen = []

    async def handler(message):
        seen.append(message)

    h = await started(fake, FakeApi(), handler)
    try:
        await wait_until(lambda: seen)
        assert seen[0]["envelope_id"] == "e1" and seen[0]["payload"]["event"]["text"] == "m1"
        await wait_until(lambda: fake.connections[0].received)
        assert fake.connections[0].received == [{"envelope_id": "e1"}]
        assert h.states()[:2] == ["connecting", "connected"]
    finally:
        await h.client.stop()


async def test_the_ack_does_not_wait_for_a_slow_handler(slack_factory):
    fake = await slack_factory(await hello_then(envelope(1)))
    release = asyncio.Event()
    finished = []

    async def handler(message):
        await release.wait()
        finished.append(message["envelope_id"])

    h = await started(fake, FakeApi(), handler)
    try:
        await wait_until(lambda: fake.connections and fake.connections[0].received)
        assert fake.connections[0].received == [{"envelope_id": "e1"}]
        assert finished == []
        release.set()
        await wait_until(lambda: finished == ["e1"])
    finally:
        await h.client.stop()


async def test_a_redelivered_event_is_acknowledged_but_handled_once(slack_factory):
    fake = await slack_factory(await hello_then(envelope(1, "EvSame"), envelope(2, "EvSame"), envelope(3)))
    seen = []

    async def handler(message):
        seen.append(message["envelope_id"])

    h = await started(fake, FakeApi(), handler)
    try:
        await wait_until(lambda: seen == ["e1", "e3"])
        await wait_until(lambda: len(fake.connections[0].received) == 3)
        assert [a["envelope_id"] for a in fake.connections[0].received] == ["e1", "e2", "e3"]
    finally:
        await h.client.stop()


async def test_envelopes_are_handled_in_order_and_a_failing_one_does_not_stop_the_rest(slack_factory):
    fake = await slack_factory(await hello_then(envelope(1), envelope(2), envelope(3)))
    seen = []

    async def handler(message):
        if message["envelope_id"] == "e2":
            raise RuntimeError("boom")
        seen.append(message["envelope_id"])

    h = await started(fake, FakeApi(), handler)
    try:
        await wait_until(lambda: seen == ["e1", "e3"])
    finally:
        await h.client.stop()


async def test_a_handler_that_hangs_is_cut_off_and_the_next_event_still_runs(slack_factory):
    fake = await slack_factory(await hello_then(envelope(1), envelope(2)))
    seen = []

    async def handler(message):
        if message["envelope_id"] == "e1":
            await asyncio.sleep(30)
        seen.append(message["envelope_id"])

    h = await started(fake, FakeApi(), handler, handler_timeout=0.2)
    try:
        await wait_until(lambda: seen == ["e2"])
    finally:
        await h.client.stop()


async def test_a_refresh_request_reconnects_with_a_new_address_at_once(slack_factory):
    async def refresh(conn):
        await conn.send(type="hello")
        await conn.send(type="disconnect", reason="refresh_requested")
        await conn.ws.wait_closed()

    fake = await slack_factory(refresh, await hello_then(envelope(1)))
    seen = []

    async def handler(message):
        seen.append(message)

    api = FakeApi()
    h = await started(fake, api, handler, stable_after=0.0)
    try:
        await wait_until(lambda: seen)
        assert api.calls == 2 and len(fake.connections) == 2
        assert h.sleeps[0] == 0.5
        assert h.states().count("connected") == 2
    finally:
        await h.client.stop()


async def test_a_lost_connection_backs_off_1_2_4_then_recovers(slack_factory):
    fake = await slack_factory(drop_without_hello, drop_without_hello, drop_without_hello, await hello_then(envelope(1)))
    seen = []

    async def handler(message):
        seen.append(message)

    h = await started(fake, FakeApi(), handler)
    try:
        await wait_until(lambda: seen)
        assert h.sleeps[:3] == [1.0, 2.0, 4.0]
        assert ("reconnecting", "ConnectionClosedError") in h.statuses
    finally:
        await h.client.stop()


async def test_the_backoff_starts_over_after_a_connection_that_worked(slack_factory):
    fake = await slack_factory(hello_then_drop, drop_without_hello, await hello_then(envelope(1)))
    seen = []

    async def handler(message):
        seen.append(message)

    h = await started(fake, FakeApi(), handler, stable_after=0.0)
    try:
        await wait_until(lambda: seen)
        assert h.sleeps[:2] == [0.5, 1.0]
    finally:
        await h.client.stop()


async def test_a_connection_that_drops_right_after_hello_still_backs_off(slack_factory):
    fake = await slack_factory(hello_then_drop, hello_then_drop, await hello_then(envelope(1)))
    seen = []

    async def handler(message):
        seen.append(message)

    h = await started(fake, FakeApi(), handler, stable_after=100.0)
    try:
        await wait_until(lambda: seen)
        assert h.sleeps[:2] == [1.0, 2.0]
    finally:
        await h.client.stop()


async def test_failing_to_get_an_address_is_retried_with_backoff(slack_factory):
    fake = await slack_factory(await hello_then(envelope(1)))
    seen = []

    async def handler(message):
        seen.append(message)

    h = await started(fake, FakeApi(SlackError("network"), SlackError("http_503")), handler)
    try:
        await wait_until(lambda: seen)
        assert h.sleeps[:2] == [1.0, 2.0]
        assert ("reconnecting", "network") in h.statuses
    finally:
        await h.client.stop()


async def test_a_token_slack_refuses_stops_the_client_and_says_why(slack_factory):
    fake = await slack_factory(hold)
    api = FakeApi(SlackAuthError("invalid_auth"))
    h = Harness(fake, api, lambda m: None)
    await asyncio.wait_for(h.client.run(), 5)
    assert h.statuses[-1] == ("failed", "invalid_auth")
    assert api.calls == 1 and h.sleeps == [] and fake.connections == []


async def test_an_address_that_is_not_slacks_is_never_connected_to(slack_factory):
    fake = await slack_factory(hold)
    api = FakeApi("wss://evil.example.com/link")
    h = Harness(fake, api, lambda m: None)
    await asyncio.wait_for(h.client.run(), 5)
    assert h.statuses[-1] == ("failed", "bad_socket_url")
    assert fake.connections == []


async def test_stop_closes_the_connection_and_ends_the_task(slack_factory):
    fake = await slack_factory(hold)
    h = await started(fake, FakeApi(), lambda m: None)
    await wait_until(lambda: "connected" in h.states())
    task = h.client._task
    await h.client.stop()
    assert task.done()
    await wait_until(lambda: fake.connections[0].ws.close_code is not None)


@pytest.mark.parametrize("url", [
    "wss://wss-primary.slack.com/link/?ticket=t",
    "wss://wss-backup.slack.com/link/",
    "wss://slack.com/x",
])
def test_slack_addresses_are_accepted(url):
    check_socket_url(url)


@pytest.mark.parametrize("url", [
    "ws://wss-primary.slack.com/link/",
    "https://wss-primary.slack.com/link/",
    "wss://evil.com/link/",
    "wss://slack.com.evil.com/link/",
    "wss://notslack.com/link/",
    "wss://user:pass@wss-primary.slack.com/link/",
    "wss://wss-primary.slack.com.evil.io/",
    "",
])
def test_other_addresses_are_refused(url):
    with pytest.raises(SlackError) as caught:
        check_socket_url(url)
    assert caught.value.code == "bad_socket_url"
