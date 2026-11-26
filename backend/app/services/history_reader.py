"""`gmail_read_sent_replies`: a bounded, staged read of the owner's past replies for the support worker (BL-174).

The worker never builds the search. When the owner has turned learning on for an account (services/history_learning.py)
this reads, on the server:

  1. the account's own address (`GMAIL_GET_PROFILE`), which identifies the owner's messages;
  2. the owner's support search, limited to the opt-in window and to mail older than the stored date cursor
     (`GMAIL_FETCH_EMAILS`, 30 per page, at most five pages), giving thread ids newest first;
  3. each thread (`GMAIL_FETCH_MESSAGE_BY_THREAD_ID`), a few at a time, keeping a thread only when the owner replied to a
     customer message in it.

and returns, for at most one stage (30 threads) per call, short redacted PAIRS: the customer message (context only, at
most 300 characters) and the owner's reply with quoted history removed (at most 1,000). Nothing in the answer is an id that
could be used to read other mail, and nothing raw is kept anywhere: only counters and digests of the threads already read
go to the state file, so a restart never reads a thread twice.

It deliberately does NOT apply the agent's share of the mailbox (BL-172): the owner opted in to this read separately, and
the read is limited to the owner's own support search window and to the number of threads the owner allowed. It is
refused when the owner has not turned it on, when no search is set, and past the stage or total bound.

Old inbound text is third-party data: a pair whose text looks like an instruction to an assistant is dropped and counted,
and the answer carries the usual notice. Customer display names from the thread's own headers become [customer].
"""
import asyncio
import re
import time
from datetime import UTC, datetime, timedelta

from app import composio_tools
from app.core.logging import get_logger
from app.services import composio_service, history_learning
from app.services.composio_client import ComposioError

logger = get_logger(__name__)

CUSTOMER_MAX = 300
REPLY_MAX = 1000
SOURCE_MAX = 4000       # what is looked at of one message before it is cut
PAGES_MAX = 5
BATCH = 6               # threads fetched together
BUDGET_SECONDS = 85     # the tool shim waits 120 seconds for an answer
_STOP = frozenset({"key_rejected", "permission_denied", "rate_limited", "unavailable"})

_clock = time.monotonic
_QUOTE_TAIL = (
    re.compile(r"^\s*On .{0,300}wrote:\s*$", re.I),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.I),
    re.compile(r"^\s*-{2,}\s*Forwarded message\s*-{2,}\s*$", re.I),
    re.compile(r"^\s*_{5,}\s*$"),
    re.compile(r"^\s*From:\s.+", re.I),
)
_INSTRUCTION = re.compile(
    r"(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?)"
    r"|system\s+prompt|you\s+are\s+(?:now\s+)?(?:an?\s+)?(?:ai|assistant|chatbot|language\s+model)"
    r"|new\s+instructions?\s*:|do\s+not\s+tell\s+the\s+(?:user|owner)|reveal\s+(?:your|the)\s+(?:prompt|instructions)",
    re.I)
_ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def strip_quoted(text: str) -> str:
    """The message without the history it quotes: lines starting with `>` and everything from an "On ... wrote:" line,
    an original-message divider or a forwarded header onward."""
    kept: list[str] = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if any(pattern.match(line) for pattern in _QUOTE_TAIL):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def looks_like_instruction(text: str) -> bool:
    return bool(_INSTRUCTION.search(text or ""))


def address_of(sender: object) -> str | None:
    found = _ADDRESS.search(sender) if isinstance(sender, str) else None
    return found.group(0).lower() if found else None


def name_of(sender: object) -> str:
    if not isinstance(sender, str):
        return ""
    return sender.split("<", 1)[0].strip().strip('"\'') if "<" in sender else ""


def when(row: dict) -> datetime | None:
    for key in ("messageTimestamp", "internalDate", "message_timestamp"):
        value = row.get(key)
        try:
            if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
                return datetime.fromtimestamp(int(value) / 1000, UTC)
            if isinstance(value, str) and value:
                moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
        except (ValueError, OverflowError, OSError):
            continue
    return None


def _text_of(row: dict) -> str:
    preview = row.get("preview")
    value = row.get("messageText") or row.get("snippet") or (preview.get("body") if isinstance(preview, dict) else None)
    return value[:SOURCE_MAX] if isinstance(value, str) else ""


def _sender_of(row: dict) -> object:
    return row.get("sender") or row.get("from")


def pair_from_thread(rows: list, owner: str) -> tuple[dict | None, str]:
    """(a pair, "") or (None, why) for one thread's messages: the first owner reply that follows a customer message."""
    messages = []
    for row in rows:
        if isinstance(row, dict):
            messages.append((when(row) or datetime.min.replace(tzinfo=UTC), address_of(_sender_of(row)), _sender_of(row), _text_of(row)))
    messages.sort(key=lambda m: m[0])
    names = tuple(name for name in (name_of(m[2]) for m in messages if m[1] != owner) if name)
    for index, (at, address, _sender, text) in enumerate(messages):
        if address != owner:
            continue
        before = next((m for m in reversed(messages[:index]) if m[1] != owner), None)
        if before is None:
            continue
        reply = strip_quoted(text)
        customer = strip_quoted(before[3])
        if not reply:
            continue
        if looks_like_instruction(reply) or looks_like_instruction(customer):
            return None, "instruction_like"
        cut = len(reply) > REPLY_MAX
        return {"date": at.date().isoformat(),
                "customer_message": history_learning.redact_pii(customer, names)[:CUSTOMER_MAX],
                "reply": history_learning.redact_pii(reply, names)[:REPLY_MAX], "reply_cut": cut}, ""
    return None, "no_reply"


async def _execute(client, session_id: str, slug: str, arguments: dict, account_id: str):
    return await composio_service._execute(client, session_id, slug, arguments, account_id)


async def _search(client, session_id: str, account_id: str, query: str, skip: set[str], need: int):
    """Thread ids (newest first, new ones only) with the date of the newest message found for each, and whether the
    search ran out."""
    tool = composio_tools.BY_NAME["gmail_fetch_emails"]
    found: dict[str, datetime | None] = {}
    token = None
    for _ in range(PAGES_MAX):
        arguments = {"user_id": "me", "query": query, "max_results": 30, "include_payload": False, "verbose": False}
        if token:
            arguments["page_token"] = token
        done = await _execute(client, session_id, "GMAIL_FETCH_EMAILS", arguments, account_id)
        if done.result_type != "completed":
            raise ComposioError("bad_response", "Composio did not run the search.")
        rows = composio_tools._rows(tool, done.data)
        if not isinstance(rows, list):
            raise ComposioError("bad_response", "The search answered in a form this app does not read.")
        for row in rows:
            thread = row.get("threadId") if isinstance(row, dict) else None
            if isinstance(thread, str) and thread and history_learning.digest(thread) not in skip and thread not in found:
                found[thread] = when(row)
                if len(found) >= need:
                    return found, False
        cursor = composio_tools._find(done.data, lambda v: isinstance(v, dict) and isinstance(v.get("nextPageToken"), str))
        token = cursor["nextPageToken"] if isinstance(cursor, dict) and cursor["nextPageToken"] else None
        if not token:
            return found, True
    return found, False


async def read(db, owner_id: str, client, session_id: str, account_id: str, context: dict | None) -> dict:
    """One batch (at most one stage). `context` is the bridge's own {"agent_id": ...}, never anything the agent sent."""
    agent_id = (context or {}).get("agent_id")
    if not isinstance(agent_id, int):
        return {"error": "This call has no agent to read for.", "code": "not_allowed"}
    settings, refusal = history_learning.check_can_read(agent_id, account_id)
    if refusal is not None:
        return refusal
    allowed = min(int(settings["allowed"]), int(settings["max_threads"]))
    need = min(history_learning.STAGE, allowed - int(settings["threads_read"]))
    now = datetime.now(UTC)
    start = now - timedelta(days=31 * int(settings["months"]))
    cursor = settings["cursor"]
    before = (datetime.fromisoformat(cursor).date() if cursor else now.date() + timedelta(days=1))
    query = f"({settings['query']}) after:{start:%Y/%m/%d} before:{before:%Y/%m/%d}"
    skip = set(settings["seen"])

    profile = await _execute(client, session_id, "GMAIL_GET_PROFILE", {"user_id": "me"}, account_id)
    shown = composio_tools._find(profile.data, lambda v: isinstance(v, dict) and isinstance(v.get("emailAddress"), str)) \
        if profile.result_type == "completed" else None
    owner = address_of(shown.get("emailAddress")) if isinstance(shown, dict) else None
    if owner is None:
        return {"error": "The account's own address could not be read, so the owner's replies cannot be told apart.",
                "code": "history_unavailable"}

    threads, exhausted = await _search(client, session_id, account_id, query, skip, need)
    thread_tool = composio_tools.BY_NAME["gmail_fetch_thread"]
    pairs: list[dict] = []
    counts = {"threads_read": 0, "skipped_no_reply": 0, "skipped_instruction_like": 0, "failed": 0, "reply_cut": 0}
    digests: list[str] = []
    oldest: datetime | None = None
    deadline = _clock() + BUDGET_SECONDS
    ordered = list(threads.items())
    stopped = False
    for offset in range(0, len(ordered), BATCH):
        if _clock() > deadline or stopped:
            exhausted = False
            break
        batch = ordered[offset:offset + BATCH]
        results = await asyncio.gather(*[
            _execute(client, session_id, "GMAIL_FETCH_MESSAGE_BY_THREAD_ID", {"user_id": "me", "thread_id": thread}, account_id)
            for thread, _ in batch], return_exceptions=True)
        for (thread, seen_at), done in zip(batch, results):
            if isinstance(done, ComposioError):
                counts["failed"] += 1
                stopped = stopped or done.kind in _STOP
                continue
            if isinstance(done, Exception) or done.result_type != "completed":
                counts["failed"] += 1
                continue
            rows = composio_tools._rows(thread_tool, done.data)
            counts["threads_read"] += 1
            digests.append(history_learning.digest(thread))
            if seen_at is not None and (oldest is None or seen_at < oldest):
                oldest = seen_at
            pair, why = pair_from_thread(rows if isinstance(rows, list) else [], owner)
            if pair is None:
                counts["skipped_instruction_like" if why == "instruction_like" else "skipped_no_reply"] += 1
                continue
            counts["reply_cut"] += 1 if pair["reply_cut"] else 0
            pairs.append(pair)
    if counts["threads_read"] == 0 and counts["failed"] and not exhausted:
        return {"error": "The mailbox could not be read right now. Try again later.", "code": "history_unavailable"}
    new_cursor = (oldest.date() + timedelta(days=1)).isoformat() if oldest else None
    history_learning.record_read(agent_id, account_id, processed=counts["threads_read"], cursor=new_cursor,
                                 digests=digests, exhausted=exhausted and counts["failed"] == 0)
    after = history_learning.settings_for(agent_id, account_id)
    left = max(0, min(int(after["allowed"]), int(after["max_threads"])) - int(after["threads_read"]))
    finished = bool(after["exhausted"]) or left == 0
    pairs.sort(key=lambda p: p["date"], reverse=True)
    return {
        "notice": composio_tools.NOTICE.format(app="gmail"), "app": "gmail", "pairs": pairs, "stage": counts,
        "remaining_in_stage": left, "done": finished,
        "next": ("This stage is finished. Stop reading: distil what you learned into lessons and submit each with "
                 "lessons_propose, then tell the owner what is waiting for review. Do not read more until the owner allows it."
                 if finished else "Call gmail_read_sent_replies again for the next batch, then distil."),
    }
