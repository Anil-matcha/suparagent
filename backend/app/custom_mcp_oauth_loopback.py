"""A one-shot loopback listener for the desktop app's OAuth callback (RFC 8252, section 7.3).

In the desktop app every request to the API needs the launch token, which a system browser
cannot send. So the sign-in returns to a short-lived listener on 127.0.0.1 that is separate from
the API: it answers only GET /callback, finishes the sign-in in-process, and closes. Its only
secret is the sign-in `state` (256 random bits, single use, bound to the signed-in owner when
the sign-in was started), the same protection any loopback redirect relies on.
"""

import asyncio
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

MAX_OPEN = 5
MAX_HEAD_BYTES = 16_384

# (state, code, error) -> (HTTP status, body, headers)
Complete = Callable[[str, str, str], Awaitable[tuple[int, bytes, dict[str, str]]]]
_open: dict[str, "Receiver"] = {}


class Receiver:
    def __init__(self, connection_id: str, complete: Complete, ttl: float):
        self.connection_id, self._complete, self._ttl = connection_id, complete, ttl
        self._server: asyncio.AbstractServer | None = None
        self._timer: asyncio.TimerHandle | None = None
        self.port = 0

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}/callback"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=MAX_HEAD_BYTES, backlog=4)
        self.port = self._server.sockets[0].getsockname()[1]
        self._timer = asyncio.get_running_loop().call_later(self._ttl, lambda: asyncio.ensure_future(self.close()))

    async def close(self) -> None:
        if _open.get(self.connection_id) is self:
            del _open[self.connection_id]
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        server, self._server = self._server, None
        if server is not None:
            server.close()
            try:
                await asyncio.wait_for(server.wait_closed(), 2)
            except Exception:  # noqa: BLE001 - a stuck client must not keep the listener alive
                pass

    async def _reply(self, writer: asyncio.StreamWriter, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        reasons = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict", 502: "Bad Gateway"}
        lines = [f"HTTP/1.1 {status} {reasons.get(status, 'Error')}", "Content-Type: text/html; charset=utf-8",
                 f"Content-Length: {len(body)}", "Connection: close", "Cache-Control: no-store"]
        for name, value in (headers or {}).items():
            if name.lower() not in ("content-length", "content-type", "connection", "cache-control"):
                lines.append(f"{name}: {value}")
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
        await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        finished = False
        try:
            try:
                async with asyncio.timeout(10):
                    head = await reader.readuntil(b"\r\n\r\n")
            except Exception:  # noqa: BLE001 - slow, oversized or broken requests are just dropped
                return
            request_line, *header_lines = head.decode("latin-1").split("\r\n")
            parts = request_line.split(" ")
            host = next((line.split(":", 1)[1].strip() for line in header_lines if line.lower().startswith("host:")), "")
            if len(parts) != 3 or host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
                await self._reply(writer, 400, b"Bad request.")  # also stops DNS-rebinding pages
                return
            if parts[0] != "GET":
                await self._reply(writer, 405, b"Method not allowed.")
                return
            target = urlsplit(parts[1])
            if target.path != "/callback":
                await self._reply(writer, 404, b"Not found.")
                return
            query = parse_qs(target.query)
            first = lambda key: query.get(key, [""])[0][:4096]  # noqa: E731
            status, body, headers = await self._complete(first("state"), first("code"), first("error"))
            await self._reply(writer, status, body, headers)
            finished = status == 200
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
            if finished:
                await self.close()  # one successful sign-in per listener


async def open_receiver(connection_id: str, complete: Complete, ttl: float) -> Receiver:
    """Listen for one sign-in on this connection; a newer start replaces an older listener."""
    previous = _open.get(connection_id)
    if previous is not None:
        await previous.close()
    while len(_open) >= MAX_OPEN:
        await next(iter(_open.values())).close()
    receiver = Receiver(connection_id, complete, ttl)
    await receiver.start()
    _open[connection_id] = receiver
    return receiver


async def close_for(connection_id: str) -> None:
    receiver = _open.get(connection_id)
    if receiver is not None:
        await receiver.close()
