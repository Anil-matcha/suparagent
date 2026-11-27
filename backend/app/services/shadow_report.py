"""The shadow-mode comparison report (BL-173): what a support worker predicted, set beside what the owner did.

A worker in shadow mode ranks and categorises the support threads it is shown and keeps its draft replies in its
notes; `gmail_triage_mark` stores each such view as a `ShadowPrediction` row and never touches Gmail. This module is the
other half: a deterministic job, with no model call, that reads each predicted thread back through the connected
account and finds out what the owner actually did.

Reads go through composio_service.run_tool with the agent's CURRENT share of the mailbox (app/gmail_share.py), so a
thread the share now hides is skipped, and counted nowhere visible: nothing in a report says how much was hidden.
A failed read, a missing account or a slow mailbox never fails the report; it returns what was computed.

Measures (thresholds are starting values, named below, to calibrate on the first 30 pairs):

  Ranking   Of the worker's top five threads per UTC day, the share the owner answered within 24 hours, beside the
            baseline of all predicted threads answered within 24 hours. Only threads old enough to judge (a full 24
            hours since the prediction) are counted. Under ten threads there is no rate ("too few").
  Drafts    The owner's first reply after the prediction is compared with the stored draft by WORD-SET JACCARD
            similarity: both texts are lowercased, stripped of quoted reply text, greeting and sign-off and
            punctuation, cut to their first COMPARE_WORDS words (the connected-app answer cuts message text at 1,000
            characters, so a longer text would look less alike), turned into sets of words, and scored as
            |A and B| / |A or B|. at or above AS_IS it is as_is, from LIGHT_EDIT it is light_edit, below it is a
            rewrite. A draft with no owner reply yet is no_reply_seen: counted, never guessed.
  Usable    (as_is + light_edit) / (as_is + light_edit + rewrite), shown only with at least MIN_EVALUATED such
            drafts. no_reply_seen is in neither side of the fraction.
  Disagree  Ranked in the top three but unanswered after seven days; answered within an hour but ranked below tenth.

The response carries only counts, ids, ranks, categories and timestamps: never draft text or reply text. Reports are
kept in memory for ten minutes per agent, window and share setting; a refresh is allowed once a minute. Draft text
older than RETENTION_DAYS is cleared on every computation (its digest stays).
"""
import asyncio
import copy
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app import composio_tools
from app.db.models import Agent, ShadowPrediction
from app.services import composio_bridge, composio_service

AS_IS = 0.9
LIGHT_EDIT = 0.6
MIN_EVALUATED = 10          # drafts with an outcome, and ranked threads old enough to judge, before any rate is shown
TOP_N_PER_DAY = 5
TOP_RANK_UNANSWERED = 3     # "ranked high"
LOW_RANK = 10               # "ranked low" means a rank above (worse than) this
ANSWER_WINDOW = timedelta(hours=24)
FAST_REPLY = timedelta(hours=1)
UNANSWERED_AFTER = timedelta(days=7)
MAX_THREADS = 100
DISAGREEMENTS_SHOWN = 20
CACHE_SECONDS = 600
REFRESH_EVERY_SECONDS = 60
READ_BUDGET_SECONDS = 60
COMPARE_WORDS = 150
RETENTION_DAYS = 90

# An answer with one of these codes means the account or Composio is unusable, so stop reading and report what we have.
STOP_CODES = {"not_configured", "not_connected", "not_allowed", "unknown_account", "account_required", "share_invalid"}

_clock = time.monotonic
_CACHE: dict[tuple, tuple[float, dict]] = {}
_REFRESHED: dict[int, float] = {}
_LOCKS: dict[int, asyncio.Lock] = {}

_ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+")
_QUOTE_START = re.compile(r"^\s*(on .{0,200}wrote:|-{2,}\s*original message|-{2,}\s*forwarded message|from:\s)", re.I)
_GREETING = re.compile(r"^\s*(hi|hello|hey|dear|good (morning|afternoon|evening))\b[^\n]{0,40}$", re.I)
_SIGN_OFF = re.compile(r"^\s*(thanks|thank you|many thanks|best|best regards|kind regards|warm regards|regards|cheers|sincerely)[ ,.!]*$", re.I)


# -- text ------------------------------------------------------------------------------------------------------------

def address_of(value: object) -> str | None:
    """The lowercase email address in a sender such as 'Ada <ada@example.com>', or None."""
    if not isinstance(value, str):
        return None
    found = _ADDRESS.search(value)
    return found.group(0).lower() if found else None


def when_of(message: dict) -> datetime | None:
    """A message's time (UTC) from `internalDate` (milliseconds since 1970) or `messageTimestamp` (ISO 8601)."""
    raw = message.get("internalDate")
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.strip().isdigit()):
        number = float(raw)
        try:
            return datetime.fromtimestamp(number / 1000 if number > 1e11 else number, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    stamp = message.get("messageTimestamp")
    if isinstance(stamp, str):
        try:
            parsed = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def words_of(text: object) -> list[str]:
    """The words that carry the answer: no quoted reply, greeting, sign-off or punctuation; at most COMPARE_WORDS."""
    if not isinstance(text, str):
        return []
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if _QUOTE_START.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        lines.append(line)
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and _GREETING.match(lines[0]):
        lines.pop(0)
    for index in range(len(lines) - 1, -1, -1):
        if _SIGN_OFF.match(lines[index]):
            lines = lines[:index]
            break
    cleaned = re.sub(r"[^\w\s]", " ", "\n".join(lines).lower())
    return cleaned.split()[:COMPARE_WORDS]


def similarity(draft: object, reply: object) -> float:
    a, b = set(words_of(draft)), set(words_of(reply))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def draft_class(score: float) -> str:
    return "as_is" if score >= AS_IS else "light_edit" if score >= LIGHT_EDIT else "rewrite"


# -- retention -------------------------------------------------------------------------------------------------------

async def purge_old_drafts(db, days: int = RETENTION_DAYS, *, now: datetime | None = None) -> int:
    """Clear the text of drafts older than `days` days; the digest and the prediction stay. Returns the rows cleared."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    result = await db.execute(update(ShadowPrediction)
                              .where(ShadowPrediction.predicted_at < cutoff, ShadowPrediction.draft_text.is_not(None))
                              .values(draft_text=None).execution_options(synchronize_session=False))
    await db.commit()
    return result.rowcount or 0


# -- the report ------------------------------------------------------------------------------------------------------

def _empty(agent_id: int, days: int, now: datetime, accounts: list[str] | None = None) -> dict:
    return {
        "agent_id": agent_id, "window_days": days, "generated_at": now.isoformat(),
        "sample": {"predictions": 0, "with_owner_reply": 0, "drafts": 0},
        "ranking": {"top5_answered_24h": {"rate": None, "n": 0}, "baseline_answered_24h": {"rate": None, "n": 0}, "too_few": True},
        "drafts": {"as_is": 0, "light_edit": 0, "rewrite": 0, "no_reply_seen": 0, "usable_rate": None, "too_few": True},
        "disagreements": [], "accounts": accounts or [],
    }


def _rate(hits: int, n: int) -> dict:
    return {"rate": round(hits / n, 3) if n >= MIN_EVALUATED else None, "n": n}


async def _read(db, rt, make_client, tool_name: str, arguments: dict, account: str, allowed: list[str], shares) -> dict | None:
    """One read through the same door every Gmail call uses, under the agent's current share. None if unusable."""
    tool = composio_tools.BY_NAME[tool_name]
    try:
        sent, _ = composio_tools.clean_arguments(tool, arguments)
    except composio_tools.BadArguments:
        return None
    try:
        return await composio_service.run_tool(db, rt.owner_id, make_client, tool, sent, None, account_id=account,
                                               allowed_account_ids=allowed, shares=shares)
    except Exception:  # noqa: BLE001  a report is best effort and never fails for one read
        return None


def first_owner_reply(messages: list, owner_address: str, after: datetime) -> tuple[datetime, str] | None:
    """The owner's first message in the thread after `after`: (its time, its text), or None."""
    best = None
    for message in messages:
        if not isinstance(message, dict):
            continue
        if address_of(message.get("sender") or message.get("from")) != owner_address:
            continue
        sent = when_of(message)
        if sent is None or sent <= after:
            continue
        if best is None or sent < best[0]:
            best = (sent, message.get("messageText") or message.get("snippet") or "")
    return best


async def compute(rt, agent_id: int, days: int, now: datetime) -> dict | None:
    """The report from the database and the mailbox, or None when the agent is not this owner's."""
    entries = await collect(rt, agent_id, now - timedelta(days=days), None, now)
    if entries is None:
        return None
    return _assemble(agent_id, days, now, entries)


async def collect(rt, agent_id: int, since: datetime, until: datetime | None, now: datetime) -> list[dict] | None:
    """One entry per predicted thread with `since <= predicted_at < until` (no upper bound when `until` is None): the
    prediction row, the thread's newest draft, and the owner's first reply read through the agent's own account under its
    current share. None when the agent is not this owner's; an empty list when it has no usable Gmail access. A read that
    fails or is hidden by the share is skipped silently (BL-173). Shared by the report and the share suggestion (BL-175)."""
    async with rt.session() as db:
        await purge_old_drafts(db, now=now)
        agent = await db.get(Agent, agent_id, populate_existing=True)
        if agent is None or agent.owner_id != rt.owner_id or agent.archived_at is not None:
            return None
        rows = list((await db.execute(
            select(ShadowPrediction)
            .where(ShadowPrediction.agent_id == agent_id, ShadowPrediction.predicted_at >= since,
                   *([ShadowPrediction.predicted_at < until] if until is not None else []))
            .order_by(ShadowPrediction.predicted_at.desc(), ShadowPrediction.id.desc()))).scalars())
        shares = agent.connector_shares or {}
        scopes = composio_bridge.apps_for(agent.connector_scopes)
        allowed_accounts = composio_bridge.accounts_for(agent.connector_account_scopes).get("gmail", [])
        if scopes.get("gmail") not in composio_tools.LEVELS or not allowed_accounts:
            return []

        seen: set = set()
        picked: list = []
        drafts: dict = {}  # a thread's newest draft, even when a later mark of the same thread carries none
        for row in rows:  # newest first
            if row.draft_text and (row.account_id, row.thread_id) not in drafts:
                drafts[(row.account_id, row.thread_id)] = row.draft_text
        for row in rows:  # newest first, one view per thread, at most MAX_THREADS threads
            key = (row.account_id, row.thread_id)
            if key in seen or row.account_id not in allowed_accounts:
                continue
            seen.add(key)
            picked.append(row)
            if len(picked) >= MAX_THREADS:
                break

        def make_client(key):
            return composio_bridge.make_client(key)

        owners: dict[str, str | None] = {}
        entries: list[dict] = []
        deadline = _clock() + READ_BUDGET_SECONDS
        for row in picked:
            if _clock() > deadline:
                break
            if row.account_id not in owners:
                profile = await _read(db, rt, make_client, "gmail_get_profile", {}, row.account_id, allowed_accounts, shares)
                shown = profile.get("result") if isinstance(profile, dict) else None
                owners[row.account_id] = address_of(shown.get("emailAddress")) if isinstance(shown, dict) else None
                if isinstance(profile, dict) and profile.get("code") in STOP_CODES:
                    break
            owner_address = owners[row.account_id]
            if owner_address is None:
                continue
            thread = await _read(db, rt, make_client, "gmail_fetch_thread", {"thread_id": row.thread_id}, row.account_id,
                                 allowed_accounts, shares)
            if not isinstance(thread, dict) or "error" in thread:
                if isinstance(thread, dict) and thread.get("code") in STOP_CODES:
                    break
                continue  # hidden by the share now, deleted, or unreadable: skipped, counted nowhere
            messages = thread.get("result")
            if not isinstance(messages, list):
                continue
            reply = first_owner_reply(messages, owner_address, row.predicted_at)
            entries.append({"row": row, "draft_text": drafts.get((row.account_id, row.thread_id)), "reply_at": reply[0] if reply else None, "reply_text": reply[1] if reply else None})

    return entries


def _assemble(agent_id: int, days: int, now: datetime, entries: list[dict]) -> dict:
    report = _empty(agent_id, days, now, sorted({e["row"].account_id for e in entries}))
    report["sample"]["predictions"] = len(entries)
    report["sample"]["with_owner_reply"] = sum(1 for e in entries if e["reply_at"] is not None)

    counts = {"as_is": 0, "light_edit": 0, "rewrite": 0, "no_reply_seen": 0}
    for entry in entries:
        if not entry["draft_text"]:
            continue
        report["sample"]["drafts"] += 1
        if entry["reply_at"] is None:
            counts["no_reply_seen"] += 1
        else:
            counts[draft_class(similarity(entry["draft_text"], entry["reply_text"]))] += 1
    evaluated = counts["as_is"] + counts["light_edit"] + counts["rewrite"]
    report["drafts"].update(counts)
    report["drafts"]["too_few"] = evaluated < MIN_EVALUATED
    report["drafts"]["usable_rate"] = (round((counts["as_is"] + counts["light_edit"]) / evaluated, 3)
                                       if evaluated >= MIN_EVALUATED else None)

    def answered(entry) -> bool:
        return entry["reply_at"] is not None and entry["reply_at"] - entry["row"].predicted_at <= ANSWER_WINDOW

    def mature(entry) -> bool:
        return entry["row"].predicted_at + ANSWER_WINDOW <= now

    by_day: dict = {}
    for entry in entries:
        by_day.setdefault(entry["row"].predicted_at.astimezone(UTC).date(), []).append(entry)
    top = [e for day in by_day.values()
           for e in sorted(day, key=lambda e: (e["row"].rank, e["row"].predicted_at))[:TOP_N_PER_DAY]]
    top_mature = [e for e in top if mature(e)]
    base_mature = [e for e in entries if mature(e)]
    report["ranking"]["top5_answered_24h"] = _rate(sum(answered(e) for e in top_mature), len(top_mature))
    report["ranking"]["baseline_answered_24h"] = _rate(sum(answered(e) for e in base_mature), len(base_mature))
    report["ranking"]["too_few"] = len(top_mature) < MIN_EVALUATED or len(base_mature) < MIN_EVALUATED

    found = []
    for entry in entries:
        row = entry["row"]
        if row.rank <= TOP_RANK_UNANSWERED and entry["reply_at"] is None and now - row.predicted_at > UNANSWERED_AFTER:
            kind = "ranked_high_unanswered"
        elif entry["reply_at"] is not None and entry["reply_at"] - row.predicted_at <= FAST_REPLY and row.rank > LOW_RANK:
            kind = "answered_fast_ranked_low"
        else:
            continue
        found.append({"thread_id": row.thread_id, "kind": kind, "rank": row.rank, "category": row.category,
                      "predicted_at": row.predicted_at.astimezone(UTC).isoformat()})
    report["disagreements"] = found[:DISAGREEMENTS_SHOWN]
    return report


def _digest(agent: Agent) -> str:
    """What the cached report depends on besides time: the share and the accounts the agent may read."""
    state = [agent.connector_shares, agent.connector_scopes, agent.connector_account_scopes]
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()[:16]


async def build(rt, agent_id: int, days: int = 28, *, refresh: bool = False, now: datetime | None = None) -> dict | None:
    """The report for one agent, from memory when it is under ten minutes old. None for an agent that is not the
    owner's. `refresh` recomputes, but at most once a minute per agent (otherwise the kept report is returned)."""
    async with rt.session() as db:
        agent = await db.get(Agent, agent_id, populate_existing=True)
        if agent is None or agent.owner_id != rt.owner_id or agent.archived_at is not None:
            return None
        key = (agent_id, days, _digest(agent))
    async with _LOCKS.setdefault(agent_id, asyncio.Lock()):
        at = _clock()
        cached = _CACHE.get(key)
        if cached is not None:
            if at - cached[0] < CACHE_SECONDS and not refresh:
                return copy.deepcopy(cached[1])
            if refresh and at - _REFRESHED.get(agent_id, float("-inf")) < REFRESH_EVERY_SECONDS:
                return copy.deepcopy(cached[1])
        report = await compute(rt, agent_id, days, now or datetime.now(UTC))
        if report is None:
            return None
        if refresh:
            _REFRESHED[agent_id] = at
        _CACHE[key] = (at, report)
        return copy.deepcopy(report)
