"""One outbound Discord Gateway connection with heartbeat and session resume."""

import asyncio
import contextlib
import json
import random
from collections import deque
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode, urlsplit, urlunsplit

import websockets
from websockets.asyncio.client import connect as ws_connect

from app.channels.discord.api import DiscordApi, DiscordAuthError, DiscordError
from app.core.logging import get_logger

logger = get_logger(__name__)
DIRECT_MESSAGES = 1 << 12
QUEUE_SIZE = 100
SEEN_SIZE = 500
FATAL_CLOSE_CODES = {4004, 4010, 4011, 4012, 4013, 4014}
NEW_SESSION_CLOSE_CODES = {4003, 4007, 4009}


def gateway_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        raise DiscordError("bad_gateway") from None
    if (parts.scheme != "wss" or not (host == "gateway.discord.gg" or
                                      (host.startswith("gateway-") and host.endswith(".discord.gg")))
            or parts.username or parts.password or port not in (None, 443)):
        raise DiscordError("bad_gateway")
    return urlunsplit(("wss", parts.netloc, parts.path or "/", urlencode({"v": 10, "encoding": "json"}), ""))


def _connect(url: str):
    return ws_connect(url, max_size=1_000_000, open_timeout=15, ping_interval=None)


class GatewayClient:
    def __init__(
        self, api: DiscordApi, token: str, on_event: Callable[[dict], Awaitable[None]], *,
        on_status: Callable[[str, str | None], None] | None = None,
        connect: Callable[[str], object] = _connect,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._api, self._token, self._on_event = api, token, on_event
        self._on_status, self._connect, self._sleep, self._jitter = on_status, connect, sleep, jitter
        self._task: asyncio.Task | None = None
        self._ws = None
        self._stopping = False
        self._sequence: int | None = None
        self._session: str | None = None
        self._resume_url: str | None = None
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._seen: deque[str] = deque(maxlen=SEEN_SIZE)
        self._seen_set: set[str] = set()
        self._send_lock = asyncio.Lock()
        self._heartbeat_ack = True

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stopping = True
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def run(self) -> None:
        worker = asyncio.create_task(self._work())
        backoff = 1.0
        try:
            while not self._stopping:
                connected_at: float | None = None
                try:
                    url = gateway_url(self._resume_url or await self._api.gateway())
                    self._status("connecting", None)
                    async with self._connect(url) as ws:
                        self._ws = ws
                        connected_at = await self._read(ws)
                except DiscordAuthError as exc:
                    self._status("failed", exc.code)
                    return
                except DiscordError as exc:
                    if exc.code == "bad_gateway":
                        self._status("failed", exc.code)
                        return
                    self._status("reconnecting", exc.code)
                except asyncio.CancelledError:
                    raise
                except websockets.exceptions.ConnectionClosed as exc:
                    if exc.code in FATAL_CLOSE_CODES:
                        self._status("failed", f"gateway_{exc.code}")
                        return
                    if exc.code in NEW_SESSION_CLOSE_CODES:
                        self._session = self._resume_url = None
                        self._sequence = None
                    self._status("reconnecting", f"gateway_{exc.code}")
                except (OSError, TimeoutError, websockets.exceptions.WebSocketException):
                    self._status("reconnecting", "gateway_connection")
                finally:
                    self._ws = None
                if not self._stopping:
                    if connected_at is not None and asyncio.get_running_loop().time() - connected_at >= 5:
                        backoff = 1.0
                    await self._sleep(backoff * (1 + 0.25 * self._jitter()))
                    backoff = min(backoff * 2, 120.0)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await worker

    async def _read(self, ws) -> float | None:
        heartbeat: asyncio.Task | None = None
        self._heartbeat_ack = True
        connected_at: float | None = None
        try:
            async for raw in ws:
                try:
                    packet = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(packet, dict):
                    continue
                op = packet.get("op")
                if op == 10:
                    data = packet.get("d")
                    interval = data.get("heartbeat_interval") if isinstance(data, dict) else None
                    if not isinstance(interval, (int, float)) or interval <= 0:
                        raise DiscordError("bad_hello")
                    heartbeat = asyncio.create_task(self._heartbeat(ws, interval / 1000))
                    if self._session and self._sequence is not None:
                        await self._send(ws, {"op": 6, "d": {
                            "token": self._token, "session_id": self._session, "seq": self._sequence,
                        }})
                    else:
                        await self._send(ws, {"op": 2, "d": {
                            "token": self._token, "intents": DIRECT_MESSAGES,
                            "properties": {"os": "windows", "browser": "suparagent", "device": "suparagent"},
                        }})
                elif op == 11:
                    self._heartbeat_ack = True
                elif op == 1:
                    await self._send_heartbeat(ws)
                elif op == 7:
                    return connected_at
                elif op == 9:
                    if packet.get("d") is not True:
                        self._session = self._resume_url = None
                        self._sequence = None
                    return
                elif op == 0:
                    event = packet.get("d")
                    if packet.get("t") == "READY" and isinstance(event, dict):
                        session, url = event.get("session_id"), event.get("resume_gateway_url")
                        if isinstance(session, str) and isinstance(url, str):
                            gateway_url(url)
                            self._session, self._resume_url = session, url
                        self._status("connected", None)
                        connected_at = asyncio.get_running_loop().time()
                    elif packet.get("t") == "RESUMED":
                        self._status("connected", None)
                        connected_at = asyncio.get_running_loop().time()
                    elif not await self._enqueue(packet):
                        # Resume from the last accepted sequence instead of losing an event.
                        await ws.close(code=4000)
                        return connected_at
                    if isinstance(packet.get("s"), int):
                        self._sequence = packet["s"]
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await heartbeat
        return connected_at

    async def _heartbeat(self, ws, interval: float) -> None:
        await self._sleep(interval * self._jitter())
        while True:
            if not self._heartbeat_ack:
                await ws.close(code=4000)
                return
            await self._send_heartbeat(ws)
            await self._sleep(interval)

    async def _send_heartbeat(self, ws) -> None:
        self._heartbeat_ack = False
        await self._send(ws, {"op": 1, "d": self._sequence})

    async def _send(self, ws, payload: dict) -> None:
        async with self._send_lock:
            await ws.send(json.dumps(payload))

    async def _enqueue(self, packet: dict) -> bool:
        event = packet.get("d")
        event_id = event.get("id") if isinstance(event, dict) else None
        if isinstance(event_id, str) and event_id in self._seen_set:
            return True
        try:
            self._queue.put_nowait(packet)
        except asyncio.QueueFull:
            logger.warning("discord events arrived faster than they were handled; resuming")
            return False
        if isinstance(event_id, str):
            if len(self._seen) == self._seen.maxlen:
                self._seen_set.discard(self._seen[0])
            self._seen.append(event_id)
            self._seen_set.add(event_id)
        # Discord requires a response to button interactions within three seconds.
        if packet.get("t") == "INTERACTION_CREATE" and isinstance(event, dict):
            interaction_id, token = event.get("id"), event.get("token")
            if isinstance(interaction_id, str) and isinstance(token, str):
                try:
                    await self._api.acknowledge(interaction_id, token)
                except DiscordError:
                    logger.warning("could not acknowledge a discord interaction")
        return True

    async def _work(self) -> None:
        while True:
            packet = await self._queue.get()
            try:
                await asyncio.wait_for(self._on_event(packet), timeout=60)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("a discord event could not be handled")

    def _status(self, state: str, detail: str | None) -> None:
        if self._on_status is not None:
            self._on_status(state, detail)
