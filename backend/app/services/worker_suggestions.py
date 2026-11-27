"""The team lead's latest worker suggestion, kept so the "Choose your team" screen
can offer "Use these suggestions" after a chat.

The lead's `suggest_workers` tool ranks bundled workers and returns template ids
with a reason. This remembers only that latest answer (ids and reasons, never
chat text) in a small file beside the other memory bookkeeping. Reading it
creates nothing: the screen pre-ticks the ids and the person still presses Add.
Ids that are not in the bundled catalog are dropped on read, and at most
MAX_IDS are returned.
"""

import json
import threading
import time

from app.services import agent_memory, memory_journal
from app.templates import catalog

MAX_IDS = 8
REASON_MAX = 200
# A suggestion older than this is not offered; the person asked a long time ago.
FRESH_SECONDS = 24 * 60 * 60

_lock = threading.Lock()


def _path():
    return memory_journal.state_root() / "worker-suggestions.json"


def _clean(suggestions) -> list[dict]:
    """Keep only known bundled ids, once each, with a short reason, up to MAX_IDS."""
    seen: set[str] = set()
    out: list[dict] = []
    for item in suggestions if isinstance(suggestions, list) else []:
        if not isinstance(item, dict):
            continue
        template_id = item.get("template_id")
        if not isinstance(template_id, str) or template_id in seen or catalog.get(template_id) is None:
            continue
        reason = item.get("reason")
        seen.add(template_id)
        out.append({"template_id": template_id, "reason": reason[:REASON_MAX] if isinstance(reason, str) else ""})
        if len(out) == MAX_IDS:
            break
    return out


def record(suggestions: list[dict], *, thread_id: int | None = None) -> None:
    """Remember the lead's latest answer. An empty answer clears nothing and records nothing."""
    clean = _clean(suggestions)
    if not clean:
        return
    path = _path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        agent_memory.write_atomic(
            path,
            json.dumps({"suggestions": clean, "thread_id": thread_id, "created_at": time.time()}),
        )


def latest(now: float | None = None) -> dict | None:
    """The latest fresh suggestion as {"suggestions": [...], "created_at": float}, or None."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("created_at"), (int, float)):
        return None
    if (now if now is not None else time.time()) - data["created_at"] > FRESH_SECONDS:
        return None
    clean = _clean(data.get("suggestions"))
    if not clean:
        return None
    return {"suggestions": clean, "created_at": float(data["created_at"])}
