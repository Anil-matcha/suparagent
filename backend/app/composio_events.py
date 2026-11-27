"""Composio webhook events, verified and reduced to ids (BL-177).

Composio delivers a project's trigger events as signed webhooks. A new-mail event carries the whole message
(sender, subject, text); this module never lets any of that past the front door:

  verify()   checks the signature (the Standard Webhooks scheme) and the age of the delivery;
  extract()  turns a verified envelope into a small dict of ids, or None for an event we do not act on.

Nothing here reads a database or the network, so it is exhaustively testable.

What is verified against a live Composio project, and what is assumed:
  - verified: the Gmail new-message event payload carries `thread_id` and `message_id`, and a webhook
    subscription returns a 64-character secret and offers the event types used below;
  - assumed (the signature scheme and envelope shape, not yet seen in a real delivery): Standard
    Webhooks headers `webhook-id`, `webhook-timestamp`, `webhook-signature` (`v1,<base64 hmac-sha256 of
    "<id>.<timestamp>.<body>">`, several space-separated), a 300 second tolerance, and an envelope
    `{id, type, timestamp, metadata: {trigger_id, connected_account_id, user_id, ...}, data: {...}}`.
    Extraction accepts the plausible variants of that shape; tests mark their fixtures unverified.
"""
import base64
import binascii
import hashlib
import hmac
import re
import time
from collections.abc import Mapping
from datetime import UTC, datetime

SIGNATURE_TOLERANCE_SECONDS = 300
MAX_EVENT_ID = 200

MESSAGE = "composio.trigger.message"
TRIGGER_DISABLED = "composio.trigger.disabled"
ACCOUNT_EXPIRED = "composio.connected_account.expired"

# Gmail's own ids are lowercase hex; Composio's trigger and account ids are short word-like strings.
_GMAIL_ID = re.compile(r"^[0-9a-f]{1,64}$")
_COMPOSIO_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TIMESTAMP = re.compile(r"^\d{1,12}$")
_PREFIX = "whsec_"


class SignatureError(Exception):
    """The delivery is not one Composio signed for us, or it is too old. The reason is never shown to the sender."""


def _header(headers: Mapping, name: str) -> str | None:
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == name:
            return value if isinstance(value, str) else None
    return None


def _keys(secret: str) -> list[bytes]:
    """The HMAC keys a stored secret can mean: its own bytes, and, behind a `whsec_` prefix, its base64 body."""
    keys = [secret.encode("utf-8")]
    if secret.startswith(_PREFIX):
        try:
            body = secret[len(_PREFIX):]
            keys.append(base64.b64decode(body + "=" * (-len(body) % 4), validate=True))
        except (binascii.Error, ValueError):
            pass
    return keys


def verify(headers: Mapping, body: bytes, secret: str, now: float | None = None) -> str:
    """The `webhook-id` of a delivery signed with `secret` and recent enough; SignatureError otherwise."""
    if not secret or not isinstance(body, (bytes, bytearray)):
        raise SignatureError("no secret or body")
    delivery_id = _header(headers, "webhook-id")
    timestamp = _header(headers, "webhook-timestamp")
    signatures = _header(headers, "webhook-signature")
    if not delivery_id or not timestamp or not signatures or len(delivery_id) > MAX_EVENT_ID:
        raise SignatureError("missing headers")
    if not _TIMESTAMP.match(timestamp) or abs((time.time() if now is None else now) - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
        raise SignatureError("stale or invalid timestamp")
    signed = delivery_id.encode("utf-8") + b"." + timestamp.encode("ascii") + b"." + bytes(body)
    expected = [hmac.new(key, signed, hashlib.sha256).digest() for key in _keys(secret)]
    matched = False
    for part in signatures.split():
        scheme, _, value = part.partition(",")
        if scheme != "v1":
            continue
        try:
            given = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            continue
        for candidate in expected:  # every candidate is compared, so the time taken does not say which matched
            matched = hmac.compare_digest(given, candidate) or matched
    if not matched:
        raise SignatureError("no signature matched")
    return delivery_id


def _text(value: object, pattern: re.Pattern) -> str | None:
    return value if isinstance(value, str) and pattern.match(value) else None


def _first(source: object, names: tuple[str, ...]) -> object:
    if not isinstance(source, dict):
        return None
    for name in names:
        if source.get(name) not in (None, ""):
            return source[name]
    return None


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def extract(event: object, webhook_id: str | None = None) -> dict | None:
    """The ids of a verified envelope, or None for anything we do not act on.

    A new-mail event gives `{"kind": "message", event_id, trigger_id, connected_account_id, thread_id, message_id,
    received_at, bad_ids}`. A status event (a trigger Composio disabled, an account that expired) gives
    `{"kind": "status", "status": "trigger_disabled" | "account_expired", event_id, trigger_id, connected_account_id}`.
    Only id-shaped values survive; the message text, sender, subject and every other field are dropped here.
    `bad_ids` is True when a thread or message id was present but not shaped like a Gmail id (the event is then
    refused, since an id is the one place text could be smuggled in)."""
    if not isinstance(event, dict):
        return None
    event_id, kind = event.get("id"), event.get("type")
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= MAX_EVENT_ID or not event_id.isprintable() or not isinstance(kind, str):
        return None
    if webhook_id is not None and event_id != webhook_id:
        return None  # the body must be the delivery that was signed
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if kind == MESSAGE:
        trigger_id = _text(_first(metadata, ("trigger_id", "triggerId")), _COMPOSIO_ID)
        if trigger_id is None:
            return None
        account = _text(_first(metadata, ("connected_account_id", "connectedAccountId")), _COMPOSIO_ID)
        # The payload sits in `data`; some envelopes wrap it once more in `data.payload`.
        payload = data.get("payload") if isinstance(data.get("payload"), dict) and not _first(data, ("thread_id", "threadId")) else data
        raw_thread = _first(payload, ("thread_id", "threadId"))
        raw_message = _first(payload, ("message_id", "messageId", "id"))
        thread, message = _text(raw_thread, _GMAIL_ID), _text(raw_message, _GMAIL_ID)
        bad = (raw_thread is not None and thread is None) or (raw_message is not None and message is None)
        return {"kind": "message", "event_id": event_id, "trigger_id": trigger_id, "connected_account_id": account,
                "thread_id": thread, "message_id": message, "received_at": _moment(event.get("timestamp")), "bad_ids": bad}
    if kind == TRIGGER_DISABLED:
        trigger_id = _text(_first(metadata, ("trigger_id",)) or _first(data, ("trigger_id", "id")), _COMPOSIO_ID)
        if trigger_id is None:
            return None
        return {"kind": "status", "status": "trigger_disabled", "event_id": event_id, "trigger_id": trigger_id,
                "connected_account_id": None}
    if kind == ACCOUNT_EXPIRED:
        account = _text(_first(data, ("id",)) or _first(metadata, ("connected_account_id",)), _COMPOSIO_ID)
        if account is None:
            return None
        return {"kind": "status", "status": "account_expired", "event_id": event_id, "trigger_id": None,
                "connected_account_id": account}
    return None
