"""See and undo the labels a support worker applied in Gmail (BL-173 slice 4).

The label tools (the other half of BL-173) record every label an agent applies to a thread as a
`WorkerLabelAction`. This module is what the owner uses afterwards, from the agent's settings:

  summary()  how many labels the worker has applied, on how many threads, by label name, and how many of them can
             be removed right now;
  undo()     remove exactly the recorded label id from exactly the recorded thread, one call each, and mark the
             action `undone`.

Undo is an owner action, not an agent tool. It is written here as its own small function and is never listed to an
agent. Its rules, which the tests pin:

  * It removes a label from a thread and nothing else: the call is GMAIL_MODIFY_THREAD_LABELS with
    `remove_label_ids` set to the one recorded id and no ids to add. It never deletes the label definition, never
    touches a label that is not in `worker_label` with the same name and id, and never touches another agent's or
    owner's rows.
  * It ignores the agent's share of the mailbox (it only ever touches threads the app itself labelled) but not the
    account selection: an account the agent no longer has Gmail access to is refused without a call, and counted
    as failed.
  * It is idempotent and resumable. A success marks the action `undone` at once (one commit per action), a failure
    leaves it `applied` to be retried by the next undo, and a thread or label already gone at Gmail counts as
    already removed.
  * It is bounded: at most MAX_PER_REQUEST actions and UNDO_BUDGET_SECONDS per request. What is left stays
    `applied`; the answer carries counts only and says nothing about how many remain.
  * Only one undo per agent runs at a time.

No audit entry is written: the existing audit helpers (agent history, team events) record profile and team changes,
not mailbox actions, and each action row already carries its own `undone_at`.
"""
import asyncio
import re
import time
from datetime import UTC, datetime

from sqlalchemy import distinct, func, select

from app.core.logging import get_logger
from app.db.models import Agent, WorkerLabel, WorkerLabelAction
from app.services import composio_bridge, composio_service
from app.services.composio_client import ComposioError

logger = get_logger(__name__)

SLUG = "GMAIL_MODIFY_THREAD_LABELS"
MAX_PER_REQUEST = 500
UNDO_BUDGET_SECONDS = 60
PAUSE_SECONDS = 0.05  # a small pause between calls, so a long undo does not hammer the mailbox
PREFIX = "Worker/"

# What Gmail, through Composio, says when the thread or the label is already gone.
_GONE = re.compile(r"not found|404|does not exist|no such|doesn't exist|requested entity", re.I)
# Composio trouble that will not get better by trying the next action.
_STOP = frozenset({"key_rejected", "permission_denied", "rate_limited", "unavailable"})

_clock = time.monotonic
_locks: dict[int, asyncio.Lock] = {}


def _gmail_accounts(agent: Agent) -> list[str]:
    """The Gmail accounts the agent is currently selected for (empty when Gmail is off for it)."""
    if composio_bridge.apps_for(agent.connector_scopes).get("gmail") is None:
        return []
    return composio_bridge.accounts_for(agent.connector_account_scopes).get("gmail", [])


async def _live_agent(db, rt, agent_id: int) -> Agent | None:
    agent = await db.get(Agent, agent_id, populate_existing=True)
    if agent is None or agent.owner_id != rt.owner_id or agent.archived_at is not None:
        return None
    return agent


async def _active_accounts(db, rt, client, record) -> set[str] | None:
    """Ids of the connected Gmail accounts that are active, or None when Composio cannot say."""
    try:
        found = await client.accounts(record["user_id"], "gmail")
    except ComposioError:
        return None
    return {a.id for a in found if a.status == "ACTIVE" and a.toolkit == "gmail"}


async def summary(rt, agent_id: int) -> dict | None:
    """The agent's applied labels, or None when the agent is not this owner's."""
    async with rt.session() as db:
        agent = await _live_agent(db, rt, agent_id)
        if agent is None:
            return None
        applied = (WorkerLabelAction.agent_id == agent_id, WorkerLabelAction.owner_id == rt.owner_id,
                   WorkerLabelAction.status == "applied")
        total, threads, last = (await db.execute(
            select(func.count(WorkerLabelAction.id), func.count(distinct(WorkerLabelAction.thread_id)),
                   func.max(WorkerLabelAction.applied_at)).where(*applied))).one()
        by_name = (await db.execute(
            select(WorkerLabelAction.label_name, func.count(WorkerLabelAction.id)).where(*applied)
            .group_by(WorkerLabelAction.label_name).order_by(WorkerLabelAction.label_name))).all()
        by_account = dict((await db.execute(
            select(WorkerLabelAction.account_id, func.count(WorkerLabelAction.id)).where(*applied)
            .group_by(WorkerLabelAction.account_id))).all())
        selected = set(_gmail_accounts(agent))
        undoable = 0
        candidates = {account: n for account, n in by_account.items() if account in selected}
        if candidates:
            record = await composio_service.load(db, rt.owner_id)
            if record is not None:
                client = composio_bridge.make_client(record["api_key"])
                active = await _active_accounts(db, rt, client, record)
                # When Composio cannot say, nothing is offered as removable rather than a number that may be wrong.
                undoable = sum(n for account, n in candidates.items() if active is not None and account in active)
    return {
        "agent_id": agent_id,
        "applied": int(total or 0),
        "threads": int(threads or 0),
        "labels": [{"name": name, "count": int(count)} for name, count in by_name],
        "last_applied_at": last.isoformat() if last is not None else None,
        "undoable": undoable,
        "accounts": sorted(by_account),
    }


async def _remove(client, session_id: str, action: WorkerLabelAction) -> object:
    """One removal call: this label id from this thread, and nothing else."""
    arguments = {"user_id": "me", "thread_id": action.thread_id, "remove_label_ids": [action.gmail_label_id]}
    return await composio_service._execute(client, session_id, SLUG, arguments, action.account_id)


async def undo(rt, agent_id: int) -> dict | None:
    """Remove the labels this agent applied. Counts only; None when the agent is not this owner's."""
    lock = _locks.setdefault(agent_id, asyncio.Lock())
    async with lock:
        return await _undo(rt, agent_id)


async def _undo(rt, agent_id: int) -> dict | None:
    counts = {"removed": 0, "already_removed": 0, "failed": 0}
    deadline = _clock() + UNDO_BUDGET_SECONDS
    async with rt.session() as db:
        agent = await _live_agent(db, rt, agent_id)
        if agent is None:
            return None
        selected = set(_gmail_accounts(agent))
        actions = list((await db.execute(
            select(WorkerLabelAction).where(
                WorkerLabelAction.agent_id == agent_id, WorkerLabelAction.owner_id == rt.owner_id,
                WorkerLabelAction.status == "applied").order_by(WorkerLabelAction.id).limit(MAX_PER_REQUEST))).scalars())
        if not actions:
            return counts
        # The label definitions the app made: the only (name, id) pairs a removal may name.
        known = {(row.account_id, row.name, row.gmail_label_id) for row in (await db.execute(
            select(WorkerLabel).where(WorkerLabel.owner_id == rt.owner_id))).scalars()}

        record = await composio_service.load(db, rt.owner_id)
        client = composio_bridge.make_client(record["api_key"]) if record is not None else None
        active = await _active_accounts(db, rt, client, record) if client is not None else None
        session_id = None
        stopped = False

        for action in actions:
            if stopped or _clock() > deadline:
                break
            allowed = (action.account_id in selected and action.label_name.startswith(PREFIX)
                       and (action.account_id, action.label_name, action.gmail_label_id) in known
                       and active is not None and action.account_id in active)
            if not allowed:  # no call is made for an account the agent lost, a label we did not record, or no key
                counts["failed"] += 1
                continue
            try:
                if session_id is None:
                    session_id, record = await composio_service._session(db, rt.owner_id, client, record)
                try:
                    done = await _remove(client, session_id, action)
                except ComposioError as exc:
                    if exc.kind != "not_found":
                        raise
                    # Composio forgot the session: replace it once, then try this action again.
                    session_id, record = await composio_service._session(db, rt.owner_id, client, record, replace=session_id)
                    done = await _remove(client, session_id, action)
            except ComposioError as exc:
                counts["failed"] += 1
                stopped = exc.kind in _STOP
                continue
            except Exception:  # noqa: BLE001  one action's trouble never fails the whole request
                logger.exception("could not remove a worker label", agent_id=agent_id)
                counts["failed"] += 1
                continue
            if done.result_type == "completed":
                counts["removed"] += 1
            elif done.result_type == "failed" and done.error and _GONE.search(done.error):
                counts["already_removed"] += 1  # the thread or the label is already gone at Gmail
            else:
                counts["failed"] += 1
                continue
            action.status = "undone"
            action.undone_at = datetime.now(UTC)
            await db.commit()  # one commit per action: a crash or a timeout loses nothing already done
            if PAUSE_SECONDS:
                await asyncio.sleep(PAUSE_SECONDS)
    return counts
