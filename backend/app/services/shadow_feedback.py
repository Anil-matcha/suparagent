"""The owner's feedback on what a support worker predicted in shadow mode (BL-175).

One row per agent, account, thread and kind ("draft" or "ranking"): changing your mind replaces the row. The reason is
one of a fixed list so it can be counted. The optional note is bounded and stripped of secrets and personal details and is
never used as an instruction. Nothing here holds or returns mail text.

A thread may be given feedback only if the app recorded a prediction for it and it is inside the agent's CURRENT share of
the mailbox (app/gmail_share.py): the share applies to what the owner can attach feedback to, exactly as it does to what
the worker can read."""
import re
from datetime import UTC, datetime

from sqlalchemy import func, select

from app import gmail_share
from app.db.models import Agent, ShadowFeedback, ShadowPrediction
from app.db.models.shadow_feedback import SHADOW_FEEDBACK_KINDS, SHADOW_FEEDBACK_REASONS
from app.services import agent_memory, composio_bridge, shadow_marks

NOTE_MAX = 300
THREAD_ID_MAX = 200


class FeedbackError(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def clean_note(note: object) -> str | None:
    """The note without secrets or personal details, one line, at most NOTE_MAX characters; None when empty."""
    if note is None:
        return None
    if not isinstance(note, str) or len(note) > NOTE_MAX:
        raise FeedbackError(422, "invalid_note", f"Keep the note to {NOTE_MAX} characters or fewer.")
    text = shadow_marks.redact_personal(agent_memory.redact_secrets(note)[0])
    text = re.sub(r"\s+", " ", text).strip()[:NOTE_MAX]
    return text or None


async def record(db, owner_id: str, agent_id: int, *, thread_id: object, kind: object, reason: object, note: object = None,
                 account_id: object = None, now: datetime | None = None) -> dict:
    """Store (or replace) the owner's feedback and return {thread_id, kind, reason, recorded}."""
    if kind not in SHADOW_FEEDBACK_KINDS:
        raise FeedbackError(422, "invalid_kind", "Feedback is about a draft or a ranking.")
    if reason not in SHADOW_FEEDBACK_REASONS:
        raise FeedbackError(422, "invalid_reason", "Choose one of the listed reasons.")
    if not isinstance(thread_id, str) or not thread_id or len(thread_id) > THREAD_ID_MAX:
        raise FeedbackError(422, "invalid_thread", "Give the thread the worker looked at.")
    if account_id is not None and (not isinstance(account_id, str) or not account_id):
        raise FeedbackError(422, "invalid_account", "The account must be a connected Gmail account.")
    text = clean_note(note)
    agent = await db.get(Agent, agent_id, populate_existing=True)
    if agent is None or agent.owner_id != owner_id or agent.archived_at is not None:
        raise FeedbackError(404, "agent_not_found", "No agent with that id.")
    rows = list((await db.execute(
        select(ShadowPrediction).where(ShadowPrediction.agent_id == agent_id, ShadowPrediction.thread_id == thread_id)
        .order_by(ShadowPrediction.predicted_at.desc(), ShadowPrediction.id.desc()))).scalars())
    if account_id is not None:
        rows = [row for row in rows if row.account_id == account_id]
    if not rows:
        raise FeedbackError(404, "no_prediction", "The worker has no recorded prediction for that thread.")
    account = rows[0].account_id
    if account not in composio_bridge.accounts_for(agent.connector_account_scopes).get("gmail", []):
        raise FeedbackError(409, "account_not_allowed", "This agent no longer uses that Gmail account.")
    try:
        rule = gmail_share.rule_for(agent.connector_shares, "gmail", account)
    except gmail_share.ShareInvalid:
        raise FeedbackError(409, "share_invalid", "This agent's share of mail is not set up correctly.") from None
    if not gmail_share.in_share(rule, thread_id):
        raise FeedbackError(403, "outside_share", "That thread is outside the share of mail this agent may use.")
    at = now or datetime.now(UTC)
    existing = (await db.execute(select(ShadowFeedback).where(
        ShadowFeedback.agent_id == agent_id, ShadowFeedback.account_id == account,
        ShadowFeedback.thread_id == thread_id, ShadowFeedback.kind == kind))).scalar_one_or_none()
    if existing is None:
        db.add(ShadowFeedback(owner_id=owner_id, agent_id=agent_id, account_id=account, thread_id=thread_id, kind=kind,
                              reason=reason, note=text, created_at=at))
    else:
        existing.reason, existing.note, existing.created_at = reason, text, at
    await db.commit()
    return {"thread_id": thread_id, "kind": kind, "reason": reason, "recorded": True}


async def counts(db, owner_id: str, agent_id: int, *, since: datetime) -> dict | None:
    """How much feedback there is since `since`, by reason and by kind. Counts only. None when the agent is not the owner's."""
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.owner_id != owner_id or agent.archived_at is not None:
        return None
    by_reason = {reason: 0 for reason in SHADOW_FEEDBACK_REASONS}
    by_kind = {kind: 0 for kind in SHADOW_FEEDBACK_KINDS}
    for reason, kind, number in (await db.execute(
            select(ShadowFeedback.reason, ShadowFeedback.kind, func.count()).where(
                ShadowFeedback.agent_id == agent_id, ShadowFeedback.created_at >= since)
            .group_by(ShadowFeedback.reason, ShadowFeedback.kind))).all():
        by_reason[reason] = by_reason.get(reason, 0) + number
        by_kind[kind] = by_kind.get(kind, 0) + number
    return {"total": sum(by_kind.values()), "by_reason": by_reason, "by_kind": by_kind}


async def count_reason(db, agent_id: int, reason: str, since: datetime) -> int:
    """How many feedback rows carry `reason` since `since` (the share suggestion holds back on wrong_policy)."""
    return int((await db.execute(select(func.count()).select_from(ShadowFeedback).where(
        ShadowFeedback.agent_id == agent_id, ShadowFeedback.reason == reason, ShadowFeedback.created_at >= since))).scalar_one())


async def for_lessons(db, owner_id: str, agent_id: int, *, since: datetime, limit: int = 200) -> list[dict]:
    """The feedback rows a pending-lessons flow (BL-174) may learn from: thread reference, kind, reason, the cleaned note and
    the time, newest first. No mail text exists in these rows. Empty for an agent that is not the owner's."""
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.owner_id != owner_id or agent.archived_at is not None:
        return []
    rows = (await db.execute(select(ShadowFeedback).where(ShadowFeedback.agent_id == agent_id, ShadowFeedback.created_at >= since)
                             .order_by(ShadowFeedback.created_at.desc(), ShadowFeedback.id.desc()).limit(limit))).scalars()
    return [{"thread_id": r.thread_id, "account_id": r.account_id, "kind": r.kind, "reason": r.reason, "note": r.note,
             "created_at": r.created_at.isoformat()} for r in rows]
