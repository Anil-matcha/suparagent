"""The desktop's end of the new-mail relay in the hosted account service (BL-177).

A desktop has no public address, so Composio's webhook points at the relay (overlay/backend/app/relay in the hosted
service). This module is the HTTP side: register or delete the account's route, acknowledge handled events, and open the
one push stream (server-sent events). It signs in with the desktop's own account session and never logs a route id, an
event id, a token or a body. What the relay says on an error is never passed on, only a short code.

The relay's paths, all under `<account service>/relay` (desktop.account.AccountManager.relay_credentials):
  POST   /routes {secret}  -> 201 {route_id, public_url}   (again with a new secret: same address, secret replaced)
  DELETE /routes/{route_id}
  POST   /ack {upto}
  GET    /stream           server-sent events: `id: <seq>`, `event: composio`, data = ids only; resumes with Last-Event-ID
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import ipaddress
import re
from urllib.parse import urlsplit

import httpx

TRUSTED_RELAY_HOST = "suparagent.ai"
TRUSTED_RELAY_HOSTS = {TRUSTED_RELAY_HOST, "staging.suparagent.ai"}
TRUSTED_RELAY_PATH = "/api/v1/accounts/relay"
ROUTE_ID = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
MAX_FRAME_BYTES = 32 * 1024     # one event is a few hundred bytes; anything larger is not ours
MAX_LINE_BYTES = 16 * 1024
# A heartbeat arrives every 25 seconds, so a silent minute and a half means the connection is dead.
STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=10.0)
CALL_TIMEOUT = httpx.Timeout(15.0)


class RelayError(Exception):
    """A short code, never the relay's own words."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


@dataclass(frozen=True)
class RelaySession:
    base_url: str
    token: str = field(repr=False)


def current_session() -> RelaySession | None:
    """The desktop's signed-in account as a relay session, or None (not a desktop, or nobody signed in)."""
    try:
        from app.desktop import account, profile
        if not profile.active():
            return None
        found = account.get_manager().relay_credentials()
    except Exception:  # noqa: BLE001
        return None
    return RelaySession(found[0], found[1]) if found else None


@dataclass(frozen=True)
class Frame:
    id: int | None
    event: str
    data: str | None      # None when the frame was larger than allowed: the caller drops it and still acknowledges it


class FrameParser:
    """Server-sent events, incrementally. Comments and unknown fields are ignored; every line and every frame is bounded."""

    def __init__(self) -> None:
        self._buffer = b""
        self._id: int | None = None
        self._event = "message"
        self._data: list[str] = []
        self._size = 0
        self._oversize = False

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buffer += chunk
        out: list[Frame] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) > MAX_LINE_BYTES:
                    raise RelayError(502, "line_too_long")
                return out
            line, self._buffer = self._buffer[:newline].rstrip(b"\r"), self._buffer[newline + 1:]
            if len(line) > MAX_LINE_BYTES:
                raise RelayError(502, "line_too_long")
            frame = self._line(line)
            if frame is not None:
                out.append(frame)

    def _line(self, line: bytes) -> Frame | None:
        if not line:  # a blank line ends the frame
            frame = None
            if self._data or self._oversize:
                frame = Frame(self._id, self._event, None if self._oversize else "\n".join(self._data))
            self._id, self._event, self._data, self._size, self._oversize = None, "message", [], 0, False
            return frame
        if line.startswith(b":"):
            return None
        name, _, value = line.partition(b":")
        value = value[1:] if value.startswith(b" ") else value
        if name == b"id":
            text = value.decode("ascii", "ignore")
            self._id = int(text) if text.isdigit() and len(text) <= 18 else None
        elif name == b"event":
            self._event = value.decode("ascii", "ignore")[:40] or "message"
        elif name == b"data":
            self._size += len(value)
            if self._size > MAX_FRAME_BYTES:
                self._oversize, self._data = True, []
            elif not self._oversize:
                self._data.append(value.decode("utf-8", "replace"))
        return None


def _split_url(value: object):
    if (not isinstance(value, str) or not value or len(value) > 512
            or any(ord(char) <= 0x20 or ord(char) == 0x7f for char in value)
            or "\\" in value or "?" in value or "#" in value):
        return None
    try:
        parts = urlsplit(value)
        # Accessing .port validates malformed and out-of-range ports.
        port = parts.port
    except ValueError:
        return None
    if (not parts.scheme or not parts.hostname or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment):
        return None
    return parts, port


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_base_url(value: object) -> str:
    """Only send bearer credentials to the configured account relay or a loopback dev server."""
    parsed = _split_url(value)
    if parsed is None:
        raise RelayError(502, "invalid_endpoint")
    parts, port = parsed
    host = parts.hostname.lower().rstrip(".")
    path = parts.path.rstrip("/")
    trusted = (parts.scheme.lower() == "https" and host in TRUSTED_RELAY_HOSTS
               and port in (None, 443) and path == TRUSTED_RELAY_PATH)
    local_dev = (parts.scheme.lower() == "http" and _is_loopback(host) and path.endswith("/relay")
                 and "/../" not in f"/{path.strip('/')}/" and "%" not in path)
    if not (trusted or local_dev):
        raise RelayError(502, "invalid_endpoint")
    # Return a normalized URL so path concatenation cannot preserve an empty path or trailing slash ambiguity.
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = rendered_host + (f":{port}" if port is not None else "")
    return f"{parts.scheme.lower()}://{netloc}{path}"


def _validate_public_url(base_url: str, value: object, route_id: str) -> bool:
    """The Composio target must be this exact relay route on the configured relay origin."""
    base = _split_url(_validate_base_url(base_url))
    target = _split_url(value)
    if base is None or target is None or not isinstance(route_id, str) or not ROUTE_ID.fullmatch(route_id):
        return False
    base_parts, base_port = base
    target_parts, target_port = target
    expected_path = f"{base_parts.path.rstrip('/')}/hook/{route_id}"
    return (target_parts.scheme.lower() == "https"
            and target_parts.hostname.lower().rstrip(".") == base_parts.hostname.lower().rstrip(".")
            and (target_port or 443) == (base_port or 443)
            and target_parts.path == expected_path
            and len(value) <= 300)


class RelayClient:
    def __init__(self, session_provider=None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._session = session_provider or current_session
        self._transport = transport

    def available(self) -> bool:
        return self._session() is not None

    def _auth(self) -> RelaySession:
        session = self._session()
        if session is None:
            raise RelayError(401, "no_session")
        safe_base = _validate_base_url(session.base_url)
        if (not isinstance(session.token, str) or not session.token or len(session.token) > 8192
                or any(ord(char) < 0x20 or ord(char) == 0x7f for char in session.token)):
            raise RelayError(401, "no_session")
        return RelaySession(safe_base, session.token)

    def _http(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport, follow_redirects=False)

    @staticmethod
    def _fail(response: httpx.Response) -> RelayError:
        if response.status_code in (401, 403):
            return RelayError(401, "no_session")
        if response.status_code == 404:
            return RelayError(404, "no_route")
        if response.status_code == 429:
            return RelayError(429, "rate_limited")
        return RelayError(502, "relay_unavailable")

    async def register_route(self, secret: str) -> tuple[str, str]:
        """(route_id, public_url): the account's route, created or updated with this signing secret. Registering again keeps
        the address and replaces the secret."""
        if not isinstance(secret, str) or not 16 <= len(secret) <= 256:
            raise RelayError(400, "invalid_secret")
        session = self._auth()
        async with self._http(CALL_TIMEOUT) as http:
            response = await http.post(f"{session.base_url.rstrip('/')}/routes", json={"secret": secret},
                                       headers={"Authorization": f"Bearer {session.token}"})
        if response.status_code != 201:
            raise self._fail(response)
        try:
            body = response.json()
            route_id, url = body["route_id"], body["public_url"]
        except (ValueError, KeyError, TypeError):
            raise RelayError(502, "relay_unavailable") from None
        if not isinstance(route_id, str) or not ROUTE_ID.fullmatch(route_id):
            raise RelayError(502, "relay_unavailable")
        if not _validate_public_url(session.base_url, url, route_id):
            raise RelayError(502, "relay_unavailable")
        return route_id, url

    async def delete_route(self, route_id: str) -> None:
        session = self._auth()
        if not isinstance(route_id, str) or not ROUTE_ID.fullmatch(route_id):
            return
        async with self._http(CALL_TIMEOUT) as http:
            response = await http.delete(f"{session.base_url.rstrip('/')}/routes/{route_id}", headers={"Authorization": f"Bearer {session.token}"})
        if response.status_code not in (204, 404):
            raise self._fail(response)

    async def ack(self, upto: int) -> None:
        session = self._auth()
        async with self._http(CALL_TIMEOUT) as http:
            response = await http.post(f"{session.base_url.rstrip('/')}/ack", json={"upto": int(upto)},
                                       headers={"Authorization": f"Bearer {session.token}"})
        if response.status_code != 200:
            raise self._fail(response)

    @asynccontextmanager
    async def stream(self, last_seq: int = 0):
        """The push stream, as an async iterator of Frames. Ends when the server ends it (it does so after an hour) or the
        connection drops; the caller reconnects with the last sequence number it handled."""
        session = self._auth()
        headers = {"Authorization": f"Bearer {session.token}", "Accept": "text/event-stream", "Cache-Control": "no-cache"}
        if last_seq:
            headers["Last-Event-ID"] = str(int(last_seq))
        async with self._http(STREAM_TIMEOUT) as http:
            async with http.stream("GET", f"{session.base_url.rstrip('/')}/stream", headers=headers) as response:
                if response.status_code != 200:
                    raise self._fail(response)

                async def frames():
                    parser = FrameParser()
                    async for chunk in response.aiter_bytes():
                        for frame in parser.feed(chunk):
                            yield frame

                yield frames()


_client: RelayClient | None = None


def get_client() -> RelayClient:
    """The process's relay client. Tests replace this function."""
    global _client
    if _client is None:
        _client = RelayClient()
    return _client
