"""An evidence-based suggestion to raise or lower a support worker's share of the mailbox (BL-175).

READ ONLY, and never automatic. This module reads what the app already measured (how the owner's real replies compare with
the worker's drafts, services/shadow_report.py) and the owner's feedback, and returns a proposal. It never writes the share
(`agent.connector_shares`): only the owner's own request to the agent update does, and a test asserts the stored share is
byte-identical after a suggestion is computed.

All thresholds below are PROPOSALS to be tuned on real shadow-mode data, not tested values.

    raise : at least RAISE_MIN_EVALUATED drafts with a reply in the last 28 days, the Wilson 95% lower bound of the usable
            rate at least RAISE_MIN_LOWER, no wrong_policy feedback in the window, and at least RAISE_MIN_DAYS of data
    lower : at least LOWER_MIN_EVALUATED drafts and the upper bound under LOWER_MAX_UPPER, or the usable rate down by
            LOWER_DROP against the previous 28 days (both windows with at least LOWER_MIN_EVALUATED drafts)
    steps : the ladder 5, 10, 25, 50, 100 percent (a custom percent moves to the next or previous rung)

Lowering is checked before raising, so a worker that is slipping is never told to widen. Usable = a draft the owner sent as
written or lightly edited; rewritten drafts are the failures; a draft with no reply seen yet counts for neither
(services/shadow_report.py). Not implemented from the brief, and left for tuning: the rewrite-rate ceiling, the minimum for
the last 14 days, the settling time since the share last changed, and the pending-lessons condition."""
import asyncio
import copy
import math
import time
from datetime import UTC, datetime, timedelta

from app import gmail_share
from app.db.models import Agent
from app.services import composio_bridge, shadow_feedback, shadow_report

LADDER = (5, 10, 25, 50, 100)
WINDOW_DAYS = 28
RAISE_MIN_EVALUATED = 20
RAISE_MIN_LOWER = 0.60
RAISE_MIN_DAYS = 14
LOWER_MIN_EVALUATED = 15
LOWER_MAX_UPPER = 0.55
LOWER_DROP = 0.20
Z = 1.96
CACHE_SECONDS = 600
REFRESH_EVERY_SECONDS = 60

_CACHE: dict = {}
_REFRESHED: dict = {}
_LOCKS: dict = {}


def _clock() -> float:
    return time.monotonic()


def wilson(successes: int, n: int, z: float = Z) -> tuple[float, float] | None:
    """The Wilson score interval for `successes` of `n`, or None for no trials."""
    if n <= 0:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denominator), min(1.0, (centre + margin) / denominator)


def step_up(percent: int) -> int | None:
    """The next rung above `percent`, or None at the top."""
    return next((rung for rung in LADDER if rung > percent), None)


def step_down(percent: int) -> int | None:
    """The next rung below `percent`, or None at the bottom."""
    return next((rung for rung in reversed(LADDER) if rung < percent), None)


def tally(entries: list[dict]) -> dict:
    """Draft outcomes of one window: how many drafts had a reply, and how many of those were usable."""
    usable = rewritten = waiting = 0
    for entry in entries:
        if not entry["draft_text"]:
            continue
        if entry["reply_at"] is None:
            waiting += 1
            continue
        if shadow_report.draft_class(shadow_report.similarity(entry["draft_text"], entry["reply_text"])) == "rewrite":
            rewritten += 1
        else:
            usable += 1
    return {"evaluated": usable + rewritten, "usable": usable, "no_reply_seen": waiting}


def _percent(value: float) -> str:
    return f"{round(value * 100)}%"


def decide(current_percent: int, now_tally: dict, before_tally: dict, days: int, wrong_policy: int) -> dict:
    """The proposal from the numbers alone (pure): {state, suggested_percent, evidence, reason}."""
    n, usable = now_tally["evaluated"], now_tally["usable"]
    interval = wilson(usable, n)
    rate = usable / n if n else None
    evidence = {
        "evaluated": n, "usable": usable, "usable_rate": round(rate, 3) if rate is not None else None,
        "lower": round(interval[0], 3) if interval else None, "upper": round(interval[1], 3) if interval else None,
        "days": days, "wrong_policy": wrong_policy,
    }

    def answer(state: str, suggested: int | None, reason: str) -> dict:
        return {"state": state, "suggested_percent": suggested, "evidence": evidence, "reason": reason}

    shown = f"{usable} of {n} drafts" if n else "no drafts yet"
    smaller = step_down(current_percent)
    if smaller is not None and interval is not None:
        before_n = before_tally["evaluated"]
        slipped = (n >= LOWER_MIN_EVALUATED and before_n >= LOWER_MIN_EVALUATED
                   and before_tally["usable"] / before_n - usable / n >= LOWER_DROP - 1e-9)
        if n >= LOWER_MIN_EVALUATED and interval[1] < LOWER_MAX_UPPER:
            return answer("lower", smaller, f"Only {usable} of {n} drafts were used as written or lightly edited in the last 4 weeks "
                                            f"({_percent(interval[0])} to {_percent(interval[1])}). You could lower its share from "
                                            f"{current_percent}% to {smaller}% until it improves.")
        if slipped:
            return answer("lower", smaller, f"The share of drafts used as written or lightly edited fell from "
                                            f"{_percent(before_tally['usable'] / before_n)} to {_percent(usable / n)} against the "
                                            f"4 weeks before. You could lower its share from {current_percent}% to {smaller}%.")
    larger = step_up(current_percent)
    if larger is None:
        return answer("none", None, "It already sees all of your mail.")
    if n < RAISE_MIN_EVALUATED:
        return answer("none", None, f"Not enough evidence yet: {RAISE_MIN_EVALUATED - n} more drafts with a reply from you are needed "
                                    f"({n} so far). Stay at {current_percent}%.")
    if wrong_policy > 0:
        return answer("none", None, f"A draft was marked as using the wrong policy in the last 4 weeks, so raising its share is on "
                                    f"hold. Stay at {current_percent}%.")
    if days < RAISE_MIN_DAYS:
        return answer("none", None, f"The results cover {days} days; at least {RAISE_MIN_DAYS} days are needed before a change is "
                                    f"suggested. Stay at {current_percent}%.")
    if interval is not None and interval[0] < RAISE_MIN_LOWER:
        return answer("none", None, f"Stay at {current_percent}%: {shown} were used as written or lightly edited "
                                    f"({_percent(interval[0])} to {_percent(interval[1])}); raising needs the low end of that range "
                                    f"to reach {_percent(RAISE_MIN_LOWER)}.")
    return answer("raise", larger, f"{usable} of {n} drafts were used as written or lightly edited in the last 4 weeks "
                                   f"({_percent(interval[0])} to {_percent(interval[1])}). You could raise its share from "
                                   f"{current_percent}% to {larger}%.")


async def _window_counts(rt, agent_id: int, now: datetime) -> dict | None:
    """The mailbox-derived numbers for the last 28 days and the 28 before them, read through the agent's own account under its
    current share (the same reads as the shadow report). None when the agent is not this owner's."""
    present = await shadow_report.collect(rt, agent_id, now - timedelta(days=WINDOW_DAYS), None, now)
    if present is None:
        return None
    before = await shadow_report.collect(rt, agent_id, now - timedelta(days=2 * WINDOW_DAYS), now - timedelta(days=WINDOW_DAYS), now) or []
    accounts: dict[str, int] = {}
    for entry in present:
        accounts[entry["row"].account_id] = accounts.get(entry["row"].account_id, 0) + 1
    earliest = min((entry["row"].predicted_at for entry in present), default=None)
    days = max(0, (now - earliest).days) if earliest is not None else 0
    return {"now": tally(present), "before": tally(before), "days": days, "accounts": accounts}


async def build(rt, agent_id: int, *, now: datetime | None = None, refresh: bool = False) -> dict | None:
    """The suggestion for one agent: {agent_id, state, account_id, current_percent, suggested_percent, evidence, reason}.
    None for an agent that is not the owner's. The mailbox reads are kept ten minutes (`refresh` recomputes, at most once a
    minute per agent); the share and the owner's feedback are read fresh on every call. Writes nothing about the share."""
    at = now or datetime.now(UTC)
    async with rt.session() as db:
        agent = await db.get(Agent, agent_id, populate_existing=True)
        if agent is None or agent.owner_id != rt.owner_id or agent.archived_at is not None:
            return None
        allowed = composio_bridge.accounts_for(agent.connector_account_scopes).get("gmail", [])
        shares = copy.deepcopy(agent.connector_shares or {})
        key = (agent_id, shadow_report._digest(agent))
        wrong_policy = await shadow_feedback.count_reason(db, agent_id, "wrong_policy", at - timedelta(days=WINDOW_DAYS))
    async with _LOCKS.setdefault(agent_id, asyncio.Lock()):
        ticks = _clock()
        cached = _CACHE.get(key)
        recompute = refresh and ticks - _REFRESHED.get(agent_id, float("-inf")) >= REFRESH_EVERY_SECONDS
        if cached is not None and not recompute and ticks - cached[0] < CACHE_SECONDS:
            counts = copy.deepcopy(cached[1])
        else:
            counts = await _window_counts(rt, agent_id, at)
            if counts is None:
                return None
            if refresh:
                _REFRESHED[agent_id] = ticks
            _CACHE[key] = (ticks, counts)

    empty = {"evaluated": 0, "usable": 0, "usable_rate": None, "lower": None, "upper": None, "days": 0, "wrong_policy": wrong_policy}
    base = {"agent_id": agent_id, "state": "none", "account_id": None, "current_percent": 100, "suggested_percent": None, "evidence": empty}
    account = max(counts["accounts"], key=lambda a: (counts["accounts"][a], a)) if counts["accounts"] else (allowed[0] if allowed else None)
    if account is None:
        return {**base, "reason": "Give this worker Gmail access first; there is nothing to measure yet."}
    base["account_id"] = account
    try:
        rule = gmail_share.rule_for(shares, "gmail", account)
    except gmail_share.ShareInvalid:
        return {**base, "reason": "This worker's share of mail is not set up correctly, so no change is suggested."}
    current = rule.percent if rule is not None else 100
    return {**base, "current_percent": current, **decide(current, counts["now"], counts["before"], counts["days"], wrong_policy)}
