"""Support threads the owner has not answered within their answer time (BL-176).

The support worker records each thread it ranks (`shadow_prediction`, written by `gmail_triage_mark`). This module
finds the ones that have waited longer than the owner's answer time (services/support_windows.py) and have no reply from
the owner, so Needs You can say so (team/needs_you.py). It changes nothing and sends nothing.

How "no reply" is decided: the same way the shadow report decides it (services/shadow_report.py): read the thread through
the same Gmail path, under the agent's current share of the mailbox, and look for a message from the connected account's
own address after the worker's mark. Because that needs Gmail, it is done ONLY when:
  * the agent still has Gmail access to the account the mark was made on (read, labels or write),
  * the thread has already waited past its answer time (a thread that is not overdue is never read),
and it is bounded: at most MAX_CANDIDATES overdue threads are considered and MAX_READS are read per refresh, within a
time budget. A thread that cannot be checked (access removed, hidden by the share, a failed read) is NOT reported:
nothing here claims a thread is waiting unless it was actually checked. A thread the owner has answered is remembered by
the caller so it is not read again, and an overdue thread stays on the list until the owner answers it; time alone never
clears it, except that marks older than LOOKBACK_DAYS are no longer looked at.

Only ids, categories and times are returned: never a subject, a sender or any message text.
"""
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app import composio_tools
from app.db.models import Agent, ShadowPrediction
from app.services import business_profile, composio_bridge, shadow_report
from app.services.support_windows import Windows, is_overdue, windows_from_profile, working_elapsed

LOOKBACK_DAYS = 30
MAX_CANDIDATES = 50
MAX_READS = 20
READ_BUDGET_SECONDS = 15


@dataclass(frozen=True)
class Waiting:
    account_id: str
    thread_id: str
    category: str
    since: datetime            # when the worker marked it: the thread has waited at least this long
    waited_hours: float        # working hours since then


@dataclass(frozen=True)
class AgentWaiting:
    agent_id: int
    agent_name: str
    waiting: list[Waiting]     # oldest first
    more: int                  # overdue threads not read this time because of the caps


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def agents_with_marks(db, owner_id: str, now: datetime) -> list[Agent]:
    """Live agents of this owner that have recorded a ranked thread recently."""
    ids = (await db.execute(select(ShadowPrediction.agent_id).where(
        ShadowPrediction.owner_id == owner_id, ShadowPrediction.predicted_at >= now - timedelta(days=LOOKBACK_DAYS)).distinct())).scalars().all()
    if not ids:
        return []
    return list((await db.execute(select(Agent).where(
        Agent.id.in_(list(ids)), Agent.owner_id == owner_id, Agent.archived_at.is_(None)).order_by(Agent.id)
        .execution_options(populate_existing=True))).scalars())


def profile_windows() -> Windows:
    """The owner's answer times from the business profile's Hours text, or the defaults."""
    try:
        return windows_from_profile(business_profile.read()["fields"].get("hours"))
    except Exception:  # noqa: BLE001  a damaged profile never blocks the card
        return Windows()


async def waiting_for_agent(db, owner_id: str, agent: Agent, *, now: datetime, zone: ZoneInfo, make_client,
                            answered: set, windows: Windows | None = None) -> AgentWaiting | None:
    """The overdue, unanswered threads for one agent, or None when there is nothing to say or nothing can be checked."""
    windows = windows or profile_windows()
    scopes = composio_bridge.apps_for(agent.connector_scopes)
    allowed = composio_bridge.accounts_for(agent.connector_account_scopes).get("gmail", [])
    if scopes.get("gmail") not in composio_tools.LEVELS or not allowed:
        return None  # no Gmail access: no call is made and nothing is claimed
    rows = (await db.execute(select(ShadowPrediction).where(
        ShadowPrediction.agent_id == agent.id, ShadowPrediction.predicted_at >= now - timedelta(days=LOOKBACK_DAYS))
        .order_by(ShadowPrediction.predicted_at.desc(), ShadowPrediction.id.desc()))).scalars().all()

    seen: set = set()
    overdue: list[ShadowPrediction] = []
    for row in rows:  # newest mark per thread
        key = (row.account_id, row.thread_id)
        if key in seen:
            continue
        seen.add(key)
        if row.account_id not in allowed or key in answered:
            continue
        since = _aware(row.predicted_at)
        if is_overdue(row.category, since, now, zone, windows):
            overdue.append(row)
    overdue.sort(key=lambda row: (_aware(row.predicted_at), row.id))  # oldest first: they have waited longest
    considered = overdue[:MAX_CANDIDATES]
    if not considered:
        return None

    rt = SimpleNamespace(owner_id=owner_id)

    def client(key):
        return (make_client or composio_bridge.make_client)(key)

    shares = agent.connector_shares or {}
    owners: dict[str, str | None] = {}
    waiting: list[Waiting] = []
    reads = 0
    try:
        async with asyncio.timeout(READ_BUDGET_SECONDS):
            for row in considered:
                if reads >= MAX_READS:
                    break
                if row.account_id not in owners:
                    profile = await shadow_report._read(db, rt, client, "gmail_get_profile", {}, row.account_id, allowed, shares)
                    shown = profile.get("result") if isinstance(profile, dict) else None
                    owners[row.account_id] = shadow_report.address_of(shown.get("emailAddress")) if isinstance(shown, dict) else None
                    if isinstance(profile, dict) and profile.get("code") in shadow_report.STOP_CODES:
                        break
                owner_address = owners[row.account_id]
                if owner_address is None:
                    continue
                reads += 1
                thread = await shadow_report._read(db, rt, client, "gmail_fetch_thread", {"thread_id": row.thread_id},
                                                   row.account_id, allowed, shares)
                if not isinstance(thread, dict) or "error" in thread:
                    if isinstance(thread, dict) and thread.get("code") in shadow_report.STOP_CODES:
                        break
                    continue  # hidden by the share, deleted or unreadable: not reported
                messages = thread.get("result")
                if not isinstance(messages, list):
                    continue
                since = _aware(row.predicted_at)
                if shadow_report.first_owner_reply(messages, owner_address, since) is not None:
                    answered.add((row.account_id, row.thread_id))
                    continue
                waiting.append(Waiting(row.account_id, row.thread_id, row.category, since,
                                       round(working_elapsed(since, now, zone).total_seconds() / 3600, 1)))
    except TimeoutError:
        pass  # what was checked is reported; the rest is looked at next time
    if not waiting:
        return None
    return AgentWaiting(agent.id, agent.name, waiting, more=max(0, len(overdue) - reads))
