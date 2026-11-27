"""Posting into a room by webhook, without running an agent.

A room can have its own bearer secret (shown once when the owner turns it on;
only a hash is kept). A delivery with that secret adds one message to the room,
authored by a labelled "webhook" sender and marked with the `webhook` origin, so
agents that later read the room treat it as untrusted outside text. No dispatch
is queued: nobody is asked to answer, and nothing runs. The body is redacted for
secrets before it is stored. `Idempotency-Key` makes a retry post once.
"""

import hashlib
import hmac
import json
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Room, RoomWebhookReceipt
from app.db.transactions import begin_write
from app.services import thread_store
from app.services.agent_memory import redact_secrets
from app.team.service import now

MAX_TEXT_CHARS = 8000
MAX_LABEL_CHARS = 40
DEFAULT_LABEL = "Webhook"
DELIVERY_ID_CHARS = 128
RECEIPT_DAYS = 7
RATE_LIMIT = 30  # posts per room per minute
RATE_WINDOW_SECONDS = 60.0

_recent: dict[int, deque[float]] = defaultdict(deque)


class RoomWebhookRejected(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _matches(secret: str | None, expected_hash: str | None) -> bool:
    if not secret or not expected_hash:
        return False
    return hmac.compare_digest(hash_secret(secret), expected_hash)


async def _owned_room(db: AsyncSession, owner_id: str, room_id: int) -> Room:
    room = await db.scalar(select(Room).where(Room.id == room_id, Room.owner_id == owner_id).with_for_update())
    if room is None or room.deleting_at is not None:
        raise RoomWebhookRejected(404, "Room not found.")
    return room


async def enable(db: AsyncSession, owner_id: str, room_id: int) -> str:
    """Turn posting by webhook on, or replace the secret. The secret is returned here only."""
    await begin_write(db)
    room = await _owned_room(db, owner_id, room_id)
    secret = "rm_" + secrets.token_urlsafe(32)
    room.webhook_secret_hash = hash_secret(secret)
    await db.commit()
    return secret


async def disable(db: AsyncSession, owner_id: str, room_id: int) -> None:
    await begin_write(db)
    room = await _owned_room(db, owner_id, room_id)
    room.webhook_secret_hash = None
    await db.commit()


def parse_body(payload: str, content_type: str | None) -> tuple[str, str]:
    """(label, text) from a JSON body `{"text", "sender"?}` or a plain-text body."""
    label, text = DEFAULT_LABEL, payload
    if "json" in (content_type or "").lower():
        try:
            data = json.loads(payload)
        except ValueError:
            raise RoomWebhookRejected(400, "The body is not valid JSON") from None
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise RoomWebhookRejected(400, 'Send JSON like {"text": "...", "sender": "optional name"}')
        text = data["text"]
        sender = data.get("sender")
        if sender is not None:
            if not isinstance(sender, str):
                raise RoomWebhookRejected(400, "sender must be text")
            cleaned = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", sender)).strip()
            label = cleaned[:MAX_LABEL_CHARS] or DEFAULT_LABEL
    return label, text.strip()


def _throttle(room_id: int) -> None:
    clock = time.monotonic()
    window = _recent[room_id]
    while window and clock - window[0] > RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise RoomWebhookRejected(429, "Too many posts to this room; try again in a minute")
    window.append(clock)


async def accept(
    db: AsyncSession, room_id: int, secret: str | None, payload: str, content_type: str | None,
    delivery_id: str | None = None,
) -> tuple[int, int, bool]:
    """Post one message. Returns (seq, thread_id, created); `created` is False for a repeated delivery.
    An unknown room, a room without a webhook and a wrong secret are the same 401."""
    await begin_write(db)
    room = await db.scalar(select(Room).where(Room.id == room_id).with_for_update())
    if room is None or room.deleting_at is not None or not _matches(secret, room.webhook_secret_hash):
        raise RoomWebhookRejected(401, "Unknown webhook or wrong secret")
    if room.archived_at is not None:
        raise RoomWebhookRejected(409, "This room is archived")
    label, text = parse_body(payload, content_type)
    if not text:
        raise RoomWebhookRejected(400, "The message is empty")
    if len(text) > MAX_TEXT_CHARS:
        raise RoomWebhookRejected(413, f"Message longer than {MAX_TEXT_CHARS} characters")
    key_hash = payload_hash = None
    if delivery_id is not None:
        if not (1 <= len(delivery_id) <= DELIVERY_ID_CHARS) or any(not 33 <= ord(c) <= 126 for c in delivery_id):
            raise RoomWebhookRejected(400, "Idempotency-Key must be 1–128 printable ASCII characters without spaces")
        key_hash = hashlib.sha256(delivery_id.encode("ascii")).hexdigest()
        payload_hash = hashlib.sha256(f"{content_type or ''}\n{payload}".encode("utf-8")).hexdigest()
        await db.execute(delete(RoomWebhookReceipt).where(
            RoomWebhookReceipt.room_id == room_id, RoomWebhookReceipt.expires_at <= now()))
        receipt = await db.get(RoomWebhookReceipt, (room_id, key_hash))
        if receipt is not None:
            if receipt.payload_hash != payload_hash:
                raise RoomWebhookRejected(409, "Idempotency-Key was already used with a different body")
            await db.commit()
            return receipt.seq, room.thread_id, False
    _throttle(room_id)
    clean = redact_secrets(text)[0]
    event = await thread_store._add_event(
        db, room.thread_id, "room.message",
        {"author": {"kind": "webhook", "label": label}, "text": clean, "mentions": [],
         "source": {"origin": "webhook"}}, None)
    room.updated_at = now()
    if key_hash is not None:
        db.add(RoomWebhookReceipt(room_id=room_id, key_hash=key_hash, payload_hash=payload_hash,
                                  seq=event.seq, expires_at=now() + timedelta(days=RECEIPT_DAYS)))
    await db.commit()
    return event.seq, room.thread_id, True
