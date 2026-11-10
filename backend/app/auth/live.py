"""Ends a signed-in browser's or device's open event streams when its sign-in stops being valid.

The middleware checks a session when a request starts, but an event stream can stay open for
hours. Without this, a member removed from the workspace (or a revoked device) would keep
receiving events until the connection dropped by itself.

Two things end a stream: `close_sessions` (immediate, in this process: removal and revoking a
device call it) and a periodic recheck (`RECHECK_SECONDS`), which also covers a server run as
several processes, where the revoking request may have been served by another one.
"""

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Iterator

from starlette.types import ASGIApp, Message, Receive, Scope, Send

RECHECK_SECONDS = 15.0

_open: dict[int, set[asyncio.Event]] = defaultdict(set)


@contextlib.contextmanager
def watch(session_id: int) -> Iterator[asyncio.Event]:
    """An event that is set when `close_sessions` names this session."""
    stop = asyncio.Event()
    _open[session_id].add(stop)
    try:
        yield stop
    finally:
        _open[session_id].discard(stop)
        if not _open[session_id]:
            del _open[session_id]


def close_sessions(session_ids: Iterable[int]) -> None:
    for session_id in session_ids:
        for stop in list(_open.get(session_id, ())):
            stop.set()


async def serve_while_valid(
    app: ASGIApp, scope: Scope, receive: Receive, send: Send, session_id: int,
    still_valid: Callable[[], Awaitable[bool]],
) -> None:
    """Run `app` for one streaming request and cut it off once the session is no longer valid."""
    started = False

    async def tracked(message: Message) -> None:
        nonlocal started
        if message["type"] == "http.response.start":
            started = True
        await send(message)

    with watch(session_id) as stop:
        task = asyncio.ensure_future(app(scope, receive, tracked))
        try:
            while True:
                waiter = asyncio.ensure_future(stop.wait())
                done, _ = await asyncio.wait({task, waiter}, timeout=RECHECK_SECONDS,
                                             return_when=asyncio.FIRST_COMPLETED)
                waiter.cancel()
                if task in done:
                    await task  # the stream ended by itself; a failure surfaces as usual
                    return
                if stop.is_set() or not await still_valid():
                    break
        finally:
            if not task.done():
                task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        if started:
            # Finish the response cleanly so the client sees the stream end, not a broken connection.
            with contextlib.suppress(Exception):
                await send({"type": "http.response.body", "body": b"", "more_body": False})
