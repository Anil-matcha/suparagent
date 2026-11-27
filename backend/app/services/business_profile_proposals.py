"""Suggested changes to the business profile: drafts a worker leaves for the
person, never applied on their own (docs/TEMPLATES.md, "Business profile").

An agent can only add a proposal here (the `suggest_profile_change` tool); it
cannot write the profile. The person sees the pending proposals beside the
profile and chooses Accept or Dismiss. Accept writes the field through the
profile's own save, so it is size-checked, secret-checked and hash-checked like
any edit, and it is refused when the field changed since the proposal was made
unless the person chooses to overwrite it anyway.

Limits: at most `MAX_PENDING` proposals wait at once. When the queue is full a
new proposal is refused, so a worker that keeps proposing cannot push out ones
the person has not read. A worker has one pending proposal per field (a newer
one from the same worker replaces its older one). Text the person already
dismissed is not proposed again. Text that looks like a key, token or password
is refused, as in the profile itself.
"""

import json
import threading
import time
from uuid import uuid4

from app.services import agent_memory, business_profile, memory_journal
from app.services.agent_memory import redact_secrets, text_hash

MAX_PENDING = 10
MAX_DISMISSED = 100
REASON_MAX = 200

_lock = threading.Lock()


class ProposalRefused(ValueError):
    """A proposal that cannot be queued. `code` is a short machine-readable reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ProposalNotFound(LookupError):
    pass


class ProposalStale(Exception):
    """The field changed since the proposal was made. `current` is its text now."""

    def __init__(self, current: str):
        super().__init__("changed")
        self.current = current


def _path():
    return memory_journal.state_root() / "business-profile-proposals.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    items = data.get("items")
    dismissed = data.get("dismissed")
    return {
        "items": [i for i in items if isinstance(i, dict)] if isinstance(items, list) else [],
        "dismissed": [d for d in dismissed if isinstance(d, str)] if isinstance(dismissed, list) else [],
    }


def _save(data: dict) -> None:
    data["dismissed"] = data["dismissed"][-MAX_DISMISSED:]
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    agent_memory.write_atomic(path, json.dumps(data, ensure_ascii=False))


def _clean(text: str) -> str:
    return (text or "").replace("\r\n", "\n").strip()


def _key(field: str, text: str) -> str:
    return text_hash(f"{field}\n{text}")


def _public(item: dict) -> dict:
    return {
        "id": item["id"],
        "field": item["field"],
        "label": business_profile.FIELDS[item["field"]][0] if item["field"] in business_profile.FIELDS else item["field"],
        "text": item["text"],
        "reason": item.get("reason", ""),
        "agent_id": item.get("agent_id"),
        "agent_name": item.get("agent_name", ""),
        "base_text": item.get("base_text", ""),
        "created_at": item.get("created_at", 0),
    }


def list_pending() -> list[dict]:
    """Pending proposals, oldest first."""
    with _lock:
        return [_public(i) for i in _load()["items"] if i.get("field") in business_profile.FIELDS]


def propose(*, field: str, text: str, reason: str, agent_id: int, agent_name: str) -> dict:
    """Queue a proposal from an agent. Raises ProposalRefused. Never touches the profile."""
    if field not in business_profile.FIELDS:
        names = ", ".join(business_profile.FIELDS)
        raise ProposalRefused("unknown_field", f"No such profile field. Use one of: {names}.")
    label, limit = business_profile.FIELDS[field]
    text, reason = _clean(text), _clean(reason)
    if not text:
        raise ProposalRefused("empty", "Say what the new text for the field should be.")
    if len(text) > limit:
        raise ProposalRefused("too_large", f"{label} must be at most {limit:,} characters.")
    if len(reason) > REASON_MAX:
        raise ProposalRefused("reason_too_large", f"The reason must be at most {REASON_MAX} characters.")
    if redact_secrets(text)[1] or redact_secrets(reason)[1]:
        raise ProposalRefused(
            "looks_secret", "That looks like it contains a key, token or password, so it was not proposed."
        )
    with _lock:
        data = _load()
        current = business_profile.read()["fields"].get(field, "")
        if text == current.strip():
            raise ProposalRefused("unchanged", f"{label} already says that.")
        if _key(field, text) in data["dismissed"]:
            raise ProposalRefused("dismissed", "The owner already dismissed that suggestion.")
        # One pending proposal per worker per field: a newer one replaces the older.
        items = [i for i in data["items"] if not (i.get("agent_id") == agent_id and i.get("field") == field)]
        if any(i.get("field") == field and i.get("text") == text for i in items):
            raise ProposalRefused("duplicate", "That suggestion is already waiting for the owner.")
        if len(items) >= MAX_PENDING:
            raise ProposalRefused(
                "queue_full", "The owner has too many suggestions waiting. Leave this one in your notes instead."
            )
        item = {
            "id": uuid4().hex[:12],
            "field": field,
            "text": text,
            "reason": reason,
            "agent_id": agent_id,
            "agent_name": (agent_name or "")[:80],
            "base_text": current.strip(),
            "created_at": time.time(),
        }
        items.append(item)
        data["items"] = items
        _save(data)
    return _public(item)


def accept(proposal_id: str, *, overwrite: bool = False) -> dict:
    """Apply a proposal to the profile and remove it. Raises ProposalNotFound, ProposalStale and
    the profile's own errors (ProfileTooLarge, ProfileLooksSecret, ProfileConflict)."""
    with _lock:
        data = _load()
        item = next((i for i in data["items"] if i.get("id") == proposal_id), None)
        if item is None or item.get("field") not in business_profile.FIELDS:
            raise ProposalNotFound(proposal_id)
        profile = business_profile.read()
        field = item["field"]
        current = profile["fields"].get(field, "").strip()
        if not overwrite and current != (item.get("base_text") or "").strip():
            raise ProposalStale(current)
        fields = dict(profile["fields"])
        fields[field] = item["text"]
        # Written with the hash just read, so a save that lands in between is refused, not overwritten.
        saved = business_profile.write(fields, expected_hash=profile["hash"])
        data["items"] = [i for i in data["items"] if i.get("id") != proposal_id]
        _save(data)
    return saved


def dismiss(proposal_id: str) -> None:
    """Drop a proposal and remember its text so the same suggestion is not made again."""
    with _lock:
        data = _load()
        item = next((i for i in data["items"] if i.get("id") == proposal_id), None)
        if item is None:
            raise ProposalNotFound(proposal_id)
        data["items"] = [i for i in data["items"] if i.get("id") != proposal_id]
        data["dismissed"].append(_key(item.get("field", ""), item.get("text", "")))
        _save(data)
