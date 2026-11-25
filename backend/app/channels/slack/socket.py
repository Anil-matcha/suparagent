"""Slack Socket Mode: one outbound WebSocket, no public address needed.

The app-level token opens a one-time `wss://` address (apps.connections.open).
Slack sends `hello`, then envelopes; each envelope is acknowledged at once by
sending its `envelope_id` back, and only then handled, so a slow handler never
makes Slack redeliver. Slack refreshes the connection every few hours and warns
with a `disconnect` message; the client reconnects (a new address each time).
Any other loss reconnects with a backoff of 1, 2, 4... up to 60 seconds, with a
little jitter; a token Slack refuses stops the client instead of retrying, and
says why through the status callback.

Envelopes are handled one at a time, in the order they arrived, by a single
worker, so a `link` message is always handled before the message after it. A
redelivered event (same Slack `event_id`) is acknowledged and then ignored.
The connection address contains a one-time ticket, so it is never logged.
"""

import asyncio
import contextlib
import json
import random
from collections import deque
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import websockets
from websockets.asyncio.client import connect as ws_connect

from app.channels.slack.api import SlackApi, SlackAuthError, SlackError
from app.channels.slack.events import event_id_of
from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_BACKOFF_SECONDS = 60.0
STABLE_AFTER_SECONDS = 5.0
QUEUE_SIZE = 100
HANDLER_TIMEOUT_SECONDS = 60.0
SEEN_EVENTS = 500
MAX_MESSAGE_BYTES = 1_000_000


def check_socket_url(url: str) -> None:
    """Refuse an address that is not Slack's: the connection carries the
    owner's messages."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "wss" or parts.username or parts.password or not (host == "slack.com" or host.endswith(".slack.com")):
        raise SlackError("bad_socket_url")


def _default_connect(url: str):
    return ws_connect(url, max_size=MAX_MESSAGE_BYTES, open_timeout=15, ping_interval=20, ping_timeout=20)


class SocketModeClient:
    def __init__(
        self,
        api: SlackApi,
        on_envelope: Callable[[dict], Awaitable[None]],
        *,
        on_status: Callable[[str, str | None], None] | None = None,
        connect: Callable[[str], object] = _default_connect,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        stable_after: float = STABLE_AFTER_SECONDS,
        handler_timeout: float = HANDLER_TIMEOUT_SECONDS,
    ) -> None:
        self._api = api
        self._on_envelope = on_envelope
        self._on_status = on_status
        self._connect = connect
        self._sleep = sleep
        self._jitter = jitter
        self._stable_after = stable_after
        self._handler_timeout = handler_timeout
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._seen: deque[str] = deque(maxlen=SEEN_EVENTS)
        self._seen_set: set[str] = set()
        self._ws = None
        self._hello_at: float | None = None
        self._stopping = False
        self._task: asyncio.Task | None = None

    # -- running ----------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stopping = True
        ws = self._ws
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def run(self) -> None:
        """Connect and stay connected until `stop()` or a token Slack refuses."""
        loop = asyncio.get_running_loop()
        worker = asyncio.create_task(self._work())
        attempt = 0
        try:
            while not self._stopping:
                self._hello_at = None
                try:
                    url = await self._api.open_connection()
                    check_socket_url(url)
                    self._status("connecting", None)
                    async with self._connect(url) as ws:
                        self._ws = ws
                        await self._read(ws)
                except SlackAuthError as exc:
                    self._status("failed", exc.code)
                    return
                except SlackError as exc:
                    if exc.code == "bad_socket_url":
                        self._status("failed", exc.code)
                        return
                    self._status("reconnecting", exc.code)
                except asyncio.CancelledError:
                    raise
                except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as exc:
                    self._status("reconnecting", exc.__class__.__name__)
                finally:
                    self._ws = None
                if self._stopping:
                    break
                stable = self._hello_at is not None and loop.time() - self._hello_at >= self._stable_after
                if stable:
                    attempt = 0
                    delay = 0.5  # a refresh, or a drop after a good run: back quickly
                else:
                    attempt += 1
                    delay = min(MAX_BACKOFF_SECONDS, 2.0 ** (attempt - 1)) * (1 + 0.25 * self._jitter())
                await self._sleep(delay)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await worker

    # -- one connection ---------------------------------------------------------------

    async def _read(self, ws) -> None:
        loop = asyncio.get_running_loop()
        async for raw in ws:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind == "hello":
                self._hello_at = loop.time()
                self._status("connected", None)
                continue
            if kind == "disconnect":
                reason = message.get("reason")
                logger.info("slack asked for a new connection", reason=reason if isinstance(reason, str) else None)
                return
            envelope_id = message.get("envelope_id")
            if isinstance(envelope_id, str) and envelope_id:
                await ws.send(json.dumps({"envelope_id": envelope_id}))
                self._enqueue(message)

    def _enqueue(self, envelope: dict) -> None:
        event_id = event_id_of(envelope)
        if event_id is not None:
            if event_id in self._seen_set:
                return
            if len(self._seen) == self._seen.maxlen:
                self._seen_set.discard(self._seen[0])
            self._seen.append(event_id)
            self._seen_set.add(event_id)
        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            logger.warning("slack events are arriving faster than they are handled; one was dropped")

    async def _work(self) -> None:
        while True:
            envelope = await self._queue.get()
            try:
                await asyncio.wait_for(self._on_envelope(envelope), timeout=self._handler_timeout)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001  (one bad event must not stop the connection)
                logger.exception("a slack event could not be handled")

    def _status(self, state: str, detail: str | None) -> None:
        if self._on_status is not None:
            try:
                self._on_status(state, detail)
            except Exception:  # noqa: BLE001
                logger.exception("a slack status callback failed")
