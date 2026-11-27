"""Learning from the owner's past replies (BL-174): the opt-in, its bounds, and the review store for what was learned.

A new support worker knows nothing about the owner's tone or usual answers. With the owner's explicit, per-account opt-in
it may read a bounded number of the owner's recent SENT replies in support threads (services/history_reader.py), distil
them, and hand the result over as short lessons. Nothing it learns is used until the owner accepts it:

  * Opt-in (`update_settings`): off by default, per agent and per connected Gmail account, with a support Gmail search
    that must be given (never the whole mailbox), a total bound (default 100 threads, at most 200), a window (at most
    6 months) and a stage allowance (30 threads at a time) that only the owner raises. "Not now" is remembered.
  * Lessons (`propose_lesson`): redacted, bounded text kept in a state file OUTSIDE the agent's workspace (beside the
    memory journal and the proposal queues), so it is not a file the agent can list or recall. At most 40 wait at once.
  * Review (`accept_lesson`, `dismiss_lesson`): accepting a template, question or tone item appends a dated line marked
    `from history` to the worker's answer-library note through the journaled save, so the journal's Undo works; a policy
    observation goes only to the existing memory-proposal queue as a suggestion (the business profile has no field
    for a policy, and a policy is never a rule); a dismissed lesson is remembered and never proposed again.
  * Forget (`forget`): removes the lines marked `from history`, the pending lessons and the read progress.

No raw mail is kept anywhere by this module: only the lessons the worker wrote, after `redact_pii`.

What `redact_pii` does NOT catch: free-text names that are not in a message header ("tell your husband Raj"), street
addresses, dates of birth, health details, and order numbers that look like ordinary words. That is why the owner reviews
every lesson in full before it is used.
"""
import hashlib
import json
import re
import threading
import time
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.services import agent_memory, memory_journal, memory_proposals, memory_topics
from app.services.agent_memory import redact_secrets, text_hash
from app.services.shadow_marks import redact_personal

STAGE = 30                  # threads one stage may read before the owner decides
MAX_THREADS_DEFAULT = 100
MAX_THREADS_LIMIT = 200
MONTHS_DEFAULT = 6
MONTHS_LIMIT = 6
QUERY_MAX = 200
MAX_PENDING = 40
MAX_DECIDED = 500
MAX_ACCEPTED_SHOWN = 100
PATTERN_MAX = 120
TEXT_MAX = 600
SEEN_MAX = 400              # digests of threads already read, so a date cursor never reads one twice
KINDS = ("template", "tone", "question", "policy")
NOTE = "answer-library.md"
HISTORY_MARK = "from history"
LABELS = {"template": "Template", "tone": "Tone", "question": "Recurring question", "policy": "Policy observation"}

_lock = threading.Lock()


class HistoryRefused(ValueError):
    """A request that cannot be applied. `code` is a short machine-readable reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class LessonNotFound(LookupError):
    pass


# -- redaction ------------------------------------------------------------------------------------------------------

_CARD = re.compile(r"(?<![\w])(?:\d[ -]?){13,19}(?![\w])")
_LONG_DIGITS = re.compile(r"(?<![\w])\d{6,}(?![\w])")
_ORDER_ID = re.compile(r"\b[A-Za-z]{1,6}[-#]?\d{4,}\b")


def _luhn(digits: str) -> bool:
    total, flip = 0, False
    for char in reversed(digits):
        number = int(char)
        if flip:
            number *= 2
            number -= 9 if number > 9 else 0
        total += number
        flip = not flip
    return total % 10 == 0


def redact_pii(text: str, names: tuple[str, ...] = ()) -> str:
    """The text without secrets, card numbers (13 to 19 digits that pass the Luhn check), email addresses, phone-like and
    long digit runs, links with a query string, order or ticket ids (letters followed by four or more digits), and any
    customer name taken from the thread's own headers (`names`, replaced by [customer]).

    NOT caught: free-text names that are not in a header, street addresses, dates of birth and health details."""
    text = redact_secrets(text or "")[0]

    def card(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return "[card]" if 13 <= len(digits) <= 19 and _luhn(digits) else match.group(0)

    text = _CARD.sub(card, text)
    text = redact_personal(text)
    text = _LONG_DIGITS.sub("[number]", text)
    text = _ORDER_ID.sub("[order]", text)
    variants = set()
    for name in names:
        if name and name.strip():
            variants.add(name.strip())
            variants.update(part for part in re.split(r"[\s,.]+", name) if len(part) >= 4)
    if variants:
        # One pass over the original text, longest variant first, so a placeholder is never scanned again.
        pattern = "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))
        text = re.sub(rf"(?<!\w)(?:{pattern})(?!\w)", "[customer]", text, flags=re.IGNORECASE)
    return text


# -- the state file -------------------------------------------------------------------------------------------------

def _path(agent_id: int):
    return memory_journal.state_root() / "history-learning" / f"{int(agent_id)}.json"


def _load(agent_id: int) -> dict:
    try:
        data = json.loads(_path(agent_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for key, empty in (("accounts", {}), ("lessons", []), ("decided", [])):
        if not isinstance(data.get(key), type(empty)):
            data[key] = empty
    return data


def _save(agent_id: int, data: dict) -> None:
    data["decided"] = data["decided"][-MAX_DECIDED:]
    accepted = [item for item in data["lessons"] if item.get("status") == "accepted"][-MAX_ACCEPTED_SHOWN:]
    data["lessons"] = [item for item in data["lessons"] if item.get("status") == "pending"] + accepted
    target = _path(agent_id)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    agent_memory.write_atomic(target, json.dumps(data))


def remove(agent_id: int) -> None:
    """Drop the whole state (the agent was deleted)."""
    try:
        _path(agent_id).unlink()
    except OSError:
        pass


def _now() -> float:
    return time.time()


# -- the opt-in -----------------------------------------------------------------------------------------------------

def _fresh() -> dict:
    return {"enabled": False, "declined": False, "query": "", "max_threads": MAX_THREADS_DEFAULT, "months": MONTHS_DEFAULT,
            "allowed": 0, "threads_read": 0, "calls": 0, "cursor": None, "seen": [], "exhausted": False,
            "started_at": None, "last_read_at": None}


def settings_for(agent_id: int, account_id: str) -> dict:
    """One account's opt-in, with defaults filled in (never touches the file)."""
    with _lock:
        stored = _load(agent_id)["accounts"].get(account_id)
    row = _fresh()
    if isinstance(stored, dict):
        row.update({key: stored[key] for key in row if key in stored})
    return row


def public_settings(account_id: str, row: dict) -> dict:
    """What the owner sees: no digests, no cursor."""
    allowed = min(int(row["allowed"]), int(row["max_threads"]))
    return {
        "account_id": account_id, "enabled": bool(row["enabled"]), "declined": bool(row["declined"]),
        "query": row["query"], "max_threads": int(row["max_threads"]), "months": int(row["months"]),
        "allowed": allowed, "threads_read": int(row["threads_read"]),
        "stage_done": bool(row["enabled"]) and (int(row["threads_read"]) >= allowed or bool(row["exhausted"])),
        "finished": bool(row["enabled"]) and (int(row["threads_read"]) >= int(row["max_threads"]) or bool(row["exhausted"])),
        "started_at": row["started_at"], "last_read_at": row["last_read_at"],
    }


def _query(value: object) -> str:
    text = " ".join(str(value).split()) if isinstance(value, str) else ""
    if len(text) > QUERY_MAX:
        raise HistoryRefused("query_too_long", f"The Gmail search can be at most {QUERY_MAX} characters.")
    if redact_secrets(text)[1]:
        raise HistoryRefused("looks_secret", "That search looks like it contains a key or password.")
    return text


def update_settings(agent_id: int, account_id: str, *, enabled: bool | None = None, declined: bool | None = None,
                    query: str | None = None, max_threads: int | None = None, months: int | None = None,
                    extend: int | None = None, restart: bool = False) -> dict:
    """Apply the owner's choice for one account and return it. Raises HistoryRefused.

    Turning it on needs a support search (never the whole mailbox) and starts with one stage (30 threads); more
    threads need `extend`, so a later stage is always a click. `restart` begins a new one-time read (counters,
    progress and the already-read digests are cleared). `declined` is "Not now": remembered, never nags."""
    with _lock:
        data = _load(agent_id)
        row = _fresh()
        stored = data["accounts"].get(account_id)
        if isinstance(stored, dict):
            row.update({key: stored[key] for key in row if key in stored})
        if query is not None:
            row["query"] = _query(query)
        if max_threads is not None:
            if type(max_threads) is not int or not STAGE <= max_threads <= MAX_THREADS_LIMIT:
                raise HistoryRefused("bad_bound", f"The number of threads must be from {STAGE} to {MAX_THREADS_LIMIT}.")
            row["max_threads"] = max_threads
        if months is not None:
            if type(months) is not int or not 1 <= months <= MONTHS_LIMIT:
                raise HistoryRefused("bad_window", f"The window must be from 1 to {MONTHS_LIMIT} months.")
            row["months"] = months
        if restart:
            row.update({"allowed": STAGE if row["enabled"] else 0, "threads_read": 0, "calls": 0, "cursor": None,
                        "seen": [], "exhausted": False, "started_at": None, "last_read_at": None})
        if declined:
            row["declined"], row["enabled"] = True, False
        if enabled is True:
            if not row["query"]:
                raise HistoryRefused("query_required", "Give the Gmail label or search that finds your support mail first. "
                                                       "It never reads the whole mailbox.")
            if not row["enabled"]:
                row["enabled"], row["declined"] = True, False
                row["allowed"] = max(int(row["allowed"]), min(STAGE, int(row["max_threads"])))
                row["started_at"] = row["started_at"] or datetime.fromtimestamp(_now(), UTC).isoformat()
        elif enabled is False:
            row["enabled"] = False
        if extend is not None:
            if type(extend) is not int or extend < 1:
                raise HistoryRefused("bad_extend", "Say how many more threads to allow.")
            if not row["enabled"]:
                raise HistoryRefused("not_enabled", "Turn learning from past replies on first.")
            row["allowed"] = min(int(row["allowed"]) + extend, int(row["max_threads"]))
        row["allowed"] = min(int(row["allowed"]), int(row["max_threads"]))
        data["accounts"][account_id] = row
        _save(agent_id, data)
        return public_settings(account_id, row)


def check_can_read(agent_id: int, account_id: str) -> tuple[dict | None, dict | None]:
    """(settings, None) when the worker may read another batch, else (None, an error answer for the worker)."""
    row = settings_for(agent_id, account_id)
    if not row["enabled"]:
        return None, {"error": "The owner has not turned on learning from past replies for this account. Do not read history.",
                      "code": "history_not_enabled"}
    if not row["query"]:
        return None, {"error": "The owner has not given the Gmail search that finds support mail, so nothing can be read.",
                      "code": "history_needs_query"}
    if int(row["threads_read"]) >= int(row["max_threads"]) or row["exhausted"]:
        return None, {"error": "Everything the owner allowed has been read. Do not read more; propose what you learned.",
                      "code": "history_bound_reached"}
    if int(row["threads_read"]) >= min(int(row["allowed"]), int(row["max_threads"])):
        return None, {"error": "This stage is finished. The owner decides whether to read more: say what you learned and stop.",
                      "code": "history_stage_done"}
    return row, None


def record_read(agent_id: int, account_id: str, *, processed: int, cursor: str | None, digests: list[str],
                exhausted: bool) -> None:
    """Remember one batch: how many threads were read, where the date cursor is, which threads (as digests)."""
    with _lock:
        data = _load(agent_id)
        row = _fresh()
        stored = data["accounts"].get(account_id)
        if isinstance(stored, dict):
            row.update({key: stored[key] for key in row if key in stored})
        row["threads_read"] = int(row["threads_read"]) + processed
        row["calls"] = int(row["calls"]) + 1
        row["cursor"] = cursor or row["cursor"]
        row["seen"] = (list(row["seen"]) + digests)[-SEEN_MAX:]
        row["exhausted"] = bool(row["exhausted"] or exhausted)
        row["last_read_at"] = datetime.fromtimestamp(_now(), UTC).isoformat()
        data["accounts"][account_id] = row
        _save(agent_id, data)


def any_enabled(agent_id: int) -> bool:
    with _lock:
        return any(isinstance(row, dict) and row.get("enabled") for row in _load(agent_id)["accounts"].values())


def settings_list(agent_id: int, account_ids: list[str]) -> list[dict]:
    return [public_settings(account_id, settings_for(agent_id, account_id)) for account_id in account_ids]


# -- lessons --------------------------------------------------------------------------------------------------------

def _identity(kind: str, pattern: str) -> str:
    return hashlib.sha256(f"{kind}|{' '.join(pattern.lower().split())}".encode("utf-8")).hexdigest()[:32]


def _day(value: object) -> date:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise HistoryRefused("bad_date", "Give dates as YYYY-MM-DD.")


def _public(item: dict) -> dict:
    return {key: item.get(key) for key in ("id", "type", "pattern", "text", "count", "newest", "oldest", "status",
                                           "destination", "entry_id", "created_at", "decided_at")}


def propose_lesson(agent_id: int, *, lesson_type: str, pattern: str, text: str, count: int, newest: str, oldest: str) -> dict:
    """Queue one lesson for the owner's review. Raises HistoryRefused. Never writes any note."""
    if lesson_type not in KINDS:
        raise HistoryRefused("bad_type", "The type must be one of: " + ", ".join(KINDS) + ".")
    if not any_enabled(agent_id):
        raise HistoryRefused("history_not_enabled", "The owner has not turned on learning from past replies.")
    pattern, text = (" ".join(pattern.split()), " ".join(text.split()))
    if not pattern or not text:
        raise HistoryRefused("empty", "A lesson needs a pattern and its text.")
    if redact_secrets(pattern)[1] or redact_secrets(text)[1]:
        raise HistoryRefused("looks_secret", "That looks like it contains a key, token or password, so it was not kept.")
    pattern, text = redact_pii(pattern)[:PATTERN_MAX], redact_pii(text)[:TEXT_MAX]
    newer, older = _day(newest), _day(oldest)
    today = datetime.fromtimestamp(_now(), UTC).date()
    if older > newer or newer > today + timedelta(days=1):
        raise HistoryRefused("bad_date", "The oldest source cannot be after the newest, and neither can be in the future.")
    if newer < today - timedelta(days=31 * MONTHS_LIMIT):
        raise HistoryRefused("too_old", f"Evidence older than {MONTHS_LIMIT} months is not used.")
    identity = _identity(lesson_type, pattern)
    with _lock:
        data = _load(agent_id)
        if identity in data["decided"]:
            raise HistoryRefused("already_decided", "The owner already decided on that lesson. Do not propose it again.")
        pending = [item for item in data["lessons"] if item.get("status") == "pending"]
        if any(item.get("identity") == identity for item in pending):
            raise HistoryRefused("duplicate", "That lesson is already waiting for the owner.")
        if len(pending) >= MAX_PENDING:
            raise HistoryRefused("lesson_limit", f"At most {MAX_PENDING} lessons wait for the owner. Stop here and say so.")
        item = {"id": uuid4().hex[:12], "identity": identity, "type": lesson_type, "pattern": pattern, "text": text,
                "count": int(count), "newest": newest, "oldest": oldest, "status": "pending", "created_at": _now()}
        data["lessons"].append(item)
        _save(agent_id, data)
    return _public(item)


def record_from_tool(agent_id: object, arguments: dict) -> dict:
    """The `lessons_propose` tool's answer: the lesson was queued, or why not. `arguments` are already checked by
    composio_tools.clean_arguments; the agent is the bridge's own, never one the worker named."""
    if not isinstance(agent_id, int):
        return {"error": "This call has no agent to record the lesson for.", "code": "not_allowed"}
    try:
        item = propose_lesson(agent_id, lesson_type=arguments["lesson_type"], pattern=arguments["pattern"],
                              text=arguments["text"], count=arguments["count"], newest=arguments["newest"],
                              oldest=arguments["oldest"])
    except HistoryRefused as exc:
        return {"error": str(exc), "code": exc.code}
    return {"recorded": True, "waiting_for_review": len(list_lessons(agent_id)["pending"]), "type": item["type"]}


def list_lessons(agent_id: int) -> dict:
    with _lock:
        lessons = _load(agent_id)["lessons"]
    return {"pending": [_public(i) for i in lessons if i.get("status") == "pending"],
            "accepted": [_public(i) for i in lessons if i.get("status") == "accepted"]}


def _entry_line(item: dict, text: str) -> str:
    day = datetime.fromtimestamp(_now(), UTC).strftime("%Y-%m-%d")
    seen = f"seen {item['count']}x, {item['oldest']} to {item['newest']}"
    return " ".join(f"- {day} · {HISTORY_MARK} {item['newest']} · {LABELS[item['type']]}: {item['pattern']} — {text} ({seen})".split())


def accept_lesson(agent_id: int, lesson_id: str, *, text: str | None = None) -> dict:
    """Use a lesson. Template, question and tone items are appended to the answer-library note (journaled, so the journal's
    Undo works); a policy observation becomes a suggestion in the memory-proposal queue and nothing else. `text` is the
    owner's edit. Raises LessonNotFound, HistoryRefused and the notes' own errors (MemoryTooLarge, TooManyTopics...)."""
    with _lock:
        data = _load(agent_id)
        item = next((i for i in data["lessons"] if i.get("id") == lesson_id and i.get("status") == "pending"), None)
        if item is None:
            raise LessonNotFound(lesson_id)
        final = redact_pii(" ".join(text.split()))[:TEXT_MAX] if text is not None and text.strip() else item["text"]
        if item["type"] == "policy":
            proposal = memory_proposals.enqueue(
                agent_id, text=f"Observed in past replies: {item['pattern']} — {final} (seen {item['count']}x)",
                line=_entry_line(item, final))
            if proposal is None:
                raise HistoryRefused("queue_full", "The suggestion queue is full or already holds this. Decide those first.")
            destination, entry_id = "suggestions", None
        else:
            line = _entry_line(item, final)
            current = memory_topics.read_topic(agent_id, NOTE)
            before = current.text if current.exists else None
            body = current.text.rstrip() + "\n" + line + "\n" if current.text.strip() else "# Answer library\n\n" + line + "\n"
            doc, _, _ = memory_topics.write_topic(agent_id, NOTE, body, expected_hash=current.hash)
            row = memory_journal.record(agent_id, memory_journal.note_path(NOTE), actor="history", before=before, after=doc.text)
            destination, entry_id = "answer-library", row["id"] if row else None
        item.update({"status": "accepted", "text": final, "destination": destination, "entry_id": entry_id,
                     "decided_at": _now()})
        data["decided"].append(item["identity"])
        _save(agent_id, data)
        return _public(item)


def dismiss_lesson(agent_id: int, lesson_id: str) -> None:
    """Drop a lesson; the same lesson is never proposed again."""
    with _lock:
        data = _load(agent_id)
        item = next((i for i in data["lessons"] if i.get("id") == lesson_id and i.get("status") == "pending"), None)
        if item is None:
            raise LessonNotFound(lesson_id)
        data["lessons"] = [i for i in data["lessons"] if i is not item]
        data["decided"].append(item["identity"])
        _save(agent_id, data)


def forget(agent_id: int) -> dict:
    """Remove what was learned from history: the lines marked `from history` in the answer-library note (journaled, so it
    can be undone), the pending and accepted lessons, and the read progress. Switches reading off. Dismissed lessons stay
    dismissed. Suggestions already forwarded to the memory-proposal queue are left for the owner to decide there."""
    removed_lines = 0
    with _lock:
        current = memory_topics.read_topic(agent_id, NOTE)
        if current.exists:
            kept = [line for line in current.text.split("\n") if HISTORY_MARK not in line]
            removed_lines = len(current.text.split("\n")) - len(kept)
            if removed_lines:
                doc, _, _ = memory_topics.write_topic(agent_id, NOTE, "\n".join(kept), expected_hash=current.hash)
                memory_journal.record(agent_id, memory_journal.note_path(NOTE), actor="history", before=current.text, after=doc.text)
        data = _load(agent_id)
        dropped = len(data["lessons"])
        data["lessons"] = []
        for account_id, row in list(data["accounts"].items()):
            fresh = _fresh()
            fresh.update({"query": row.get("query", ""), "max_threads": row.get("max_threads", MAX_THREADS_DEFAULT),
                          "months": row.get("months", MONTHS_DEFAULT), "declined": bool(row.get("declined"))})
            data["accounts"][account_id] = fresh
        _save(agent_id, data)
    return {"entries_removed": removed_lines, "lessons_removed": dropped}


def digest(thread_id: str) -> str:
    return text_hash(f"history:{thread_id}")[:16]
