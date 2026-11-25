"""`gmail_triage_mark`: a support worker records how it ranked a mail thread (BL-173, shadow mode).

The tool writes one row to `shadow_prediction` and nothing else. It never calls Composio or Gmail, so it works
at the Read level and needs no card: the worker's ranking and the drafts it keeps in its notes are compared later
(services/shadow_report.py) with what the owner actually did, before the worker is allowed to change anything.

It runs inside composio_service.run_tool after the account is chosen, so it shares that path's account rules
(`account_required`, `not_connected`, the agent's own allowed accounts) and the BL-172 share of the mailbox: a
thread outside the share is refused and nothing is written. The agent never sends an owner, agent or account id.
"""
import hashlib
import re

from sqlalchemy import func, select

from app import gmail_share
from app.db.models import ShadowPrediction
from app.db.transactions import begin_write
from app.services import agent_memory

MAX_MARKS_PER_TURN = 100
MARK_LIMIT = {"error": f"At most {MAX_MARKS_PER_TURN} threads can be recorded in one turn. Record the rest in your next run.",
              "code": "mark_limit"}

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# A URL with a query string or fragment: those carry tokens, ids and tracking data. A plain link stays.
_URL_WITH_QUERY = re.compile(r"https?://[^\s<>\"')\]]*[?#][^\s<>\"')\]]*")
# Digits with the usual separators, at least 8 of them: phone numbers, order numbers, card and account numbers.
_DIGIT_RUN = re.compile(r"(?<![\w])\+?\d(?:[\s().-]{0,2}\d){7,}(?![\w])")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def redact_personal(text: str) -> str:
    """The text without email addresses, phone-like and long digit runs, and URLs with a query string.

    NOT caught: names, street addresses and any other free text, so the owner's review of what the
    worker kept is the real safeguard (docs/specs/support-worker/shadow-mode-and-labels.md)."""
    text = _URL_WITH_QUERY.sub("[link]", text)
    text = _EMAIL.sub("[email]", text)
    return _DIGIT_RUN.sub(lambda match: match.group(0) if _ISO_DATE.fullmatch(match.group(0)) else "[number]", text)


def redacted_draft(text: str) -> str:
    """Secrets (agent_memory.redact_secrets) first, then personal details."""
    return redact_personal(agent_memory.redact_secrets(text)[0])


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def record(db, owner_id: str, arguments: dict, account_id: str, rule, context: dict | None) -> dict:
    """Store (or replace, within the same turn) one prediction. `arguments` are already checked by
    composio_tools.clean_arguments; `rule` is this account's share rule or None (whole mailbox)."""
    agent_id = (context or {}).get("agent_id")
    turn_id = (context or {}).get("turn_id")
    if not isinstance(agent_id, int) or not isinstance(turn_id, int):
        return {"error": "This call has no turn to record the thread in.", "code": "not_allowed"}
    thread_id = arguments["thread_id"]
    if not gmail_share.in_share(rule, thread_id):
        return dict(gmail_share.OUTSIDE)
    draft_text = arguments.get("draft_text")
    draft = redacted_draft(draft_text) if draft_text else None
    await begin_write(db)
    existing = (await db.execute(select(ShadowPrediction).where(
        ShadowPrediction.agent_id == agent_id, ShadowPrediction.account_id == account_id,
        ShadowPrediction.thread_id == thread_id, ShadowPrediction.turn_id == turn_id))).scalar_one_or_none()
    if existing is None:
        marked = await db.scalar(select(func.count()).select_from(ShadowPrediction).where(
            ShadowPrediction.agent_id == agent_id, ShadowPrediction.turn_id == turn_id))
        if (marked or 0) >= MAX_MARKS_PER_TURN:
            await db.rollback()
            return dict(MARK_LIMIT)
        existing = ShadowPrediction(owner_id=owner_id, agent_id=agent_id, account_id=account_id,
                                    thread_id=thread_id, turn_id=turn_id)
        db.add(existing)
    existing.rank = arguments["rank"]
    existing.category = arguments["category"]
    existing.owner_only = bool(arguments.get("owner_only", False))
    ref = arguments.get("draft_ref")
    existing.draft_ref = ref
    existing.draft_text = draft
    existing.draft_digest = digest(draft) if draft is not None else None
    await db.commit()
    return {"recorded": True, "thread_id": thread_id, "rank": arguments["rank"]}
