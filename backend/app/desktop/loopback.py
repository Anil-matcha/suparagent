"""A one-shot listener on 127.0.0.1 that receives a browser sign-in's return.

The browser is sent here by the account service after the person signs in. The request carries a
one-time grant in its address, so this listener makes every check that does not depend on the sign-in
itself: it binds the loopback address only, the `Host` header must name that address (a web page that
rebinds a name to this machine fails), the method is GET, and only the one path is answered. The page
the browser is left on is fixed text. It never repeats anything from the request, because a page that
echoed the grant would put a credential in the browser's history.
"""

from __future__ import annotations

import asyncio
import html
from typing import Callable
from urllib.parse import parse_qs, urlsplit

READ_LIMIT = 8192
READ_SECONDS = 10.0

# (HTTP status, message) for the page the browser is left on.
Answer = tuple[int, str]


def _page(message: str) -> bytes:
    body = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Suparagent</title><body style='font:16px system-ui;max-width:28rem;margin:20vh auto;padding:0 1rem'>"
            f"<p>{html.escape(message)}</p>")
    return body.encode()


class LoopbackListener:
    def __init__(self, path: str, receive: Callable[[dict[str, str]], Answer]):
        self._path = path
        self._receive = receive
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self.port = 0

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        for writer in list(self._writers):
            writer.close()
        try:
            await asyncio.wait_for(self._server.wait_closed(), 2.0)
        except (asyncio.TimeoutError, Exception):
            pass
        self._server = None

    def _check(self, method: str, target: str, host: str | None) -> dict[str, str] | Answer:
        if (host or "").lower() not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            return 400, "This page is not part of a sign-in."
        if method not in ("GET", "HEAD"):
            return 405, "This page is not part of a sign-in."
        parts = urlsplit(target)
        if parts.path != self._path:
            return 404, "This page is not part of a sign-in."
        return {key: values[0] for key, values in parse_qs(parts.query, keep_blank_values=True).items()}

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_SECONDS)
            if len(head) > READ_LIMIT:
                raise ValueError("request too large")
            lines = head.decode("latin-1").split("\r\n")
            method, target, *_ = lines[0].split(" ") + ["", ""]
            host = next((line.split(":", 1)[1].strip() for line in lines[1:] if line.lower().startswith("host:")), None)
            checked = self._check(method, target, host)
            answer = self._receive(checked) if isinstance(checked, dict) else checked
            status, message = answer
            body = _page(message)
            writer.write((f"HTTP/1.1 {status} {'OK' if status == 200 else 'Error'}\r\n"
                          "Content-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\n"
                          f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + (b"" if method == "HEAD" else body))
            await writer.drain()
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()
