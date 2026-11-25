"""The channel-independent half of talking to an agent from a chat app.

The owner's message becomes a turn in one conversation (thread) with one agent;
the turn's stored events are read back and posted: the answer in pieces the
chat app accepts, and each approval as a card the owner can answer. Nothing
here knows Slack. The chat app side is a `ChannelAdapter` (base.py) and the
caller has already checked that the message came from the owner: the bridge
acts on whatever it is given.

A turn's events are read the same way the app's own stream reads them
(thread_stream.event_frames): subscribe to the hub first, then read stored
events after a cursor, and wait for a commit or a short poll.

Everything posted passes `redact_secrets` first: a reply or an approval that
quotes something credential-like must not be copied into a third party's chat.
"""

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable

from sqlalchemy import func, select

from app.channels.base import ApprovalCard, ChannelAdapter
from app.channels.text import chunk_text
from app.core.logging import get_logger
from app.db.models import Event
from app.services import thread_store as store
from app.services.agent_memory import redact_secrets
from app.services.thread_runtime import (
    AgentArchived,
    EngineModelInvalid,
    ImagesUnsupported,
    ProviderUnavailable,
    SessionBroken,
    UnknownAgent,
    UnknownProvider,
)
from app.services.thread_store import ThreadBusy, ThreadNotFound

logger = get_logger(__name__)

MAX_INBOUND_CHARS = 8_000
STILL_WORKING_SECONDS = 20.0
POLL_SECONDS = 1.0
MAX_ERROR_CHARS = 500
TERMINAL = {"turn.completed", "turn.failed", "turn.cancelled", "turn.interrupted"}

NEW_WORD = "new"
BUSY = "Still working on your last message. I'll reply here when it's done."
NOTHING_TO_SAY = "(The agent finished without a text reply.)"

# What `on_decision` tells the chat app.
RECORDED = "recorded"
ALREADY_ANSWERED = "already_answered"
UNKNOWN = "unknown"


class ChannelBridge:
    def __init__(
        self,
        runtime,
        adapter: ChannelAdapter,
        *,
        agent_id: int | None,
        load_thread_id: Callable[[], Awaitable[int | None]],
        save_thread_id: Callable[[int | None], Awaitable[None]],
        still_working_seconds: float = STILL_WORKING_SECONDS,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.runtime = runtime
        self.adapter = adapter
        self.agent_id = agent_id
        self._load_thread_id = load_thread_id
        self._save_thread_id = save_thread_id
        self._still_working = still_working_seconds
        self._poll = poll_seconds
        self._lock = asyncio.Lock()
        self._relays: dict[int, asyncio.Task] = {}
        # approval id -> (thread id, the adapter's handle for its card)
        self._cards: dict[str, tuple[int, object | None]] = {}

    # -- from the owner ---------------------------------------------------------------

    async def on_owner_message(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        name = self.adapter.name
        if len(text) > MAX_INBOUND_CHARS:
            await self._say(
                f"That message is too long to send from {name} (the limit is {MAX_INBOUND_CHARS:,} characters). "
                "Shorten it, or use the app."
            )
            return
        async with self._lock:
            if text.lower() == NEW_WORD:
                await self._new_conversation()
                return
            await self._send(text)

    async def on_decision(self, approval_id: str, decision: str) -> str:
        """The owner pressed Allow or Deny on a card this bridge posted."""
        if approval_id not in self._cards:
            return UNKNOWN
        won = await self.runtime.answer_approval(approval_id, decision, person_label=self.adapter.name)
        return RECORDED if won else ALREADY_ANSWERED

    async def close(self) -> None:
        for task in list(self._relays.values()):
            task.cancel()
        for task in list(self._relays.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._relays.clear()

    # -- sending ----------------------------------------------------------------------

    async def _new_conversation(self) -> None:
        if any(not task.done() for task in self._relays.values()):
            await self._say("Wait for the current reply to finish, then send `new` again.")
            return
        await self._save_thread_id(None)
        await self._say("Started a new conversation.")

    async def _thread_id(self) -> int | None:
        thread_id = await self._load_thread_id()
        if thread_id is None:
            thread = await self.runtime.create_thread(agent_id=self.agent_id)
            thread_id = thread.id
            await self._save_thread_id(thread_id)
        return thread_id

    async def _last_seq(self, thread_id: int) -> int:
        async with self.runtime.session() as db:
            found = await db.execute(select(func.max(Event.seq)).where(Event.thread_id == thread_id))
            return found.scalar() or 0

    async def _send(self, text: str) -> None:
        try:
            thread_id = await self._thread_id()
            cursor = await self._last_seq(thread_id)
            try:
                await self.runtime.send_turn(thread_id, text)
            except ThreadNotFound:
                # The conversation was deleted in the app: start another.
                await self._save_thread_id(None)
                thread_id = await self._thread_id()
                cursor = 0
                await self.runtime.send_turn(thread_id, text)
        except ThreadBusy:
            await self._say(BUSY)
            return
        except (UnknownAgent, AgentArchived):
            await self._say("The agent chosen for this chat is not available. Choose another in Settings, Integrations.")
            return
        except ProviderUnavailable as exc:
            await self._say(_short(str(exc)) or "The agent's engine is not available right now.")
            return
        except (UnknownProvider, EngineModelInvalid, SessionBroken, ImagesUnsupported) as exc:
            await self._say(f"Could not start that: {_short(str(exc))}")
            return
        except OSError:
            await self._say("The agent's folder is not available. Check the agent in the app.")
            return
        except Exception:  # noqa: BLE001  (a chat message must never crash the connection)
            logger.exception("a channel message could not be started")
            await self._say("Something went wrong starting that. Try again, or use the app.")
            return
        self._relays[thread_id] = asyncio.create_task(self._relay(thread_id, cursor))

    # -- reading the turn back --------------------------------------------------------

    async def _relay(self, thread_id: int, cursor: int) -> None:
        loop = asyncio.get_running_loop()
        subscription = self.runtime.hub.subscribe(thread_id)  # before the first read: no gap
        started = loop.time()
        warned = False
        texts: list[str] = []
        try:
            while True:
                subscription.wake.clear()
                async with self.runtime.session() as db:
                    events = await store.list_events(db, thread_id, after=cursor, limit=200)
                for event in events:
                    cursor = event.seq
                    done = await self._handle(event, texts)
                    if done:
                        return
                if len(events) == 200:
                    continue
                if not warned and loop.time() - started >= self._still_working:
                    warned = True
                    await self._say("Still working on it...")
                await subscription.wait(self._poll)
                if subscription.closed:
                    return  # the conversation was deleted
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("a channel reply could not be relayed")
            await self._say("I lost track of that reply. Check the app for the answer.")
        finally:
            self.runtime.hub.unsubscribe(subscription)
            await self._close_open_cards(thread_id, "No longer waiting")
            if self._relays.get(thread_id) is asyncio.current_task():
                self._relays.pop(thread_id, None)

    async def _handle(self, event, texts: list[str]) -> bool:
        data = event.data if isinstance(event.data, dict) else {}
        kind = event.type
        if kind == "assistant.message":
            text = data.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
        elif kind == "approval.requested":
            await self._open_card(event.thread_id, data)
        elif kind == "approval.resolved":
            await self._resolve_card(data)
        elif kind in TERMINAL:
            await self._finish(kind, data, texts)
            return True
        return False

    async def _finish(self, kind: str, data: dict, texts: list[str]) -> None:
        reply = "\n\n".join(texts)
        if kind == "turn.completed":
            await self._say(reply or NOTHING_TO_SAY, markdown=True)
            return
        if reply:
            await self._say(reply, markdown=True)
        if kind == "turn.cancelled":
            await self._say("That was stopped.")
        elif kind == "turn.interrupted":
            await self._say("That was interrupted (the app restarted). Send it again if you still want it.")
        else:
            error = _short(str(data.get("error") or "")) or "The agent could not finish."
            await self._say(f"That did not finish: {error}")

    # -- approvals --------------------------------------------------------------------

    async def _open_card(self, thread_id: int, data: dict) -> None:
        approval_id = data.get("approval_id")
        if not isinstance(approval_id, str) or approval_id in self._cards:
            return
        tool = redact_secrets(str(data.get("tool") or "a tool"))[0]
        summary = redact_secrets(str(data.get("summary") or ""))[0]
        raw_input = data.get("input")
        detail = ""
        if raw_input not in (None, {}, ""):
            try:
                detail = json.dumps(raw_input, indent=2, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                detail = str(raw_input)
            detail = redact_secrets(detail)[0]
        room = self.adapter.card_chars
        fits = len(tool) + len(summary) + len(detail) + 60 <= room
        if not fits:
            detail = _cut(detail, max(0, room - len(tool) - len(summary) - 60))
        card = ApprovalCard(approval_id, tool, summary, detail, interactive=fits)
        try:
            handle = await self.adapter.send_approval(card)
        except Exception:  # noqa: BLE001
            logger.exception("an approval card could not be posted")
            return
        self._cards[approval_id] = (thread_id, handle)

    async def _resolve_card(self, data: dict) -> None:
        approval_id = data.get("approval_id")
        entry = self._cards.pop(approval_id, None) if isinstance(approval_id, str) else None
        if entry is None:
            return
        status = data.get("status")
        person = data.get("person") if isinstance(data.get("person"), dict) else {}
        where = "" if person.get("label") == self.adapter.name else " in the app"
        outcome = {
            "allowed": f"Allowed{where}",
            "denied": f"Denied{where}",
            "expired": "Expired: nobody answered in time",
            "cancelled": "Cancelled",
        }.get(status, "Answered")
        await self._close(entry[1], outcome)

    async def _close_open_cards(self, thread_id: int, outcome: str) -> None:
        for approval_id, (card_thread, handle) in list(self._cards.items()):
            if card_thread == thread_id:
                self._cards.pop(approval_id, None)
                await self._close(handle, outcome)

    async def _close(self, handle: object | None, outcome: str) -> None:
        try:
            await self.adapter.close_approval(handle, outcome)
        except Exception:  # noqa: BLE001
            logger.exception("an approval card could not be updated")

    # -- posting ----------------------------------------------------------------------

    async def _say(self, text: str, *, markdown: bool = False) -> None:
        text = redact_secrets(text)[0]
        if markdown:
            text = self.adapter.render(text)
        for piece in chunk_text(text, self.adapter.max_chars):
            try:
                await self.adapter.send_text(piece)
            except Exception:  # noqa: BLE001  (the adapter has retried; a lost message is logged, not fatal)
                logger.exception("a channel message could not be posted")
                return


def _short(text: str) -> str:
    return _cut(" ".join(text.split()), MAX_ERROR_CHARS)


def _cut(text: str, size: int) -> str:
    return text if len(text) <= size else text[: max(0, size - 1)].rstrip() + "…"
