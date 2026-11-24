"""Spending limits for paid marketplace tools.

A marketplace tool is paid when its input takes `max_cost_usd`, the cap the caller sets for one call. Before such a
call goes out, the cap is reserved against three owner limits: per call, per run (a scheduled run or one chat turn),
and per worker per calendar month (UTC). The limits are settings only the owner changes; no agent tool reads or
writes them. The amounts are quotes, not invoices: a call settles at the cost the service reports, or at its cap when
the service reports none.
"""
import json
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import func, select

from app.core import app_settings
from app.db.models.agent import Agent
from app.db.models.app_setting import AppSetting, SettingAudit
from app.db.models.routine import Routine
from app.db.models.spend_ledger import SpendLedger
from app.db.transactions import begin_write
from app.desktop import profile

GUARD_ARGUMENT = "max_cost_usd"
LIMIT_KEYS = {
    "per_call": "SPEND_PER_CALL_CENTS",
    "per_run": "SPEND_PER_RUN_CENTS",
    "monthly": "SPEND_PER_WORKER_MONTH_CENTS",
}
REFUSAL_TEXT = {
    "per_call": "This call's cost cap is above the per-call spending limit.",
    "per_run": "This run would go over its spending limit.",
    "monthly": "This worker would go over its monthly spending limit.",
    "no_cap": "A paid call needs a cost cap greater than zero.",
}
PAUSE_REASON = "spend_limit"
# Allowed range of each limit, in cents. The same bounds the desktop settings registry enforces.
LIMIT_BOUNDS = {"per_call": (0, 100_000), "per_run": (0, 1_000_000), "monthly": (0, 10_000_000)}
# Connection-phase failures that mean the call never reached the remote tool.
NOT_RUN_CODES = frozenset({"connect_failed", "dns_failed", "unsafe_endpoint", "endpoint_mismatch", "redirect_denied",
                           "authentication_failed", "request_too_large", "bad_arguments", "unsupported_schema"})


def is_guarded(input_schema: dict | None) -> bool:
    properties = (input_schema or {}).get("properties")
    return isinstance(properties, dict) and GUARD_ARGUMENT in properties


def to_cents(value) -> int | None:
    """Round a dollar amount up to whole cents; None when it is not a usable non-negative number."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    # Float noise (0.1 + 0.2 is 0.30000000000000004) must not cost an extra cent: work at a millionth of a dollar.
    amount = amount.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return int((amount * 100).to_integral_value(rounding=ROUND_CEILING))


def limits() -> dict[str, int]:
    return {name: int(app_settings.get(key, live=True)) for name, key in LIMIT_KEYS.items()}


def _valid_limit(name: str, value) -> bool:
    low, high = LIMIT_BOUNDS[name]
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


async def effective_limits(db) -> dict[str, tuple[int, str]]:
    """Each limit in cents with where it came from: "database" (an owner change), "environment" or "default".

    The desktop app keeps the owner's choices in its settings registry. A headless or hosted install has no registry, so
    an owner change is stored in the same `app_setting` table and read here, ahead of the environment value."""
    rows = {}
    if not profile.active():
        wanted = list(LIMIT_KEYS.values())
        rows = {row.key: row for row in (await db.scalars(select(AppSetting).where(AppSetting.key.in_(wanted)))).all()}
    found = {}
    for name, key in LIMIT_KEYS.items():
        row = rows.get(key)
        if row is not None and _valid_limit(name, row.value_json):
            found[name] = (int(row.value_json), "database")
        else:
            found[name] = (int(app_settings.get(key, live=True)), app_settings.resolve(key, live=True).source)
    return found


async def save_headless_limits(db, values: dict[str, int], actor: str = "owner") -> None:
    """Store the owner's limits on an install without the desktop settings registry. One transaction, audited."""
    for name, value in values.items():
        if not _valid_limit(name, value):
            raise ValueError(f"The {name.replace('_', ' ')} limit is outside the allowed range.")
    await begin_write(db)
    now = datetime.now(UTC)
    for name, value in values.items():
        key = LIMIT_KEYS[name]
        row = await db.get(AppSetting, key, with_for_update=True)
        old = row.value_json if row is not None else None
        if row is None:
            db.add(AppSetting(key=key, value_json=value, version=1, updated_at=now, updated_by=actor))
        else:
            row.value_json, row.version, row.updated_at, row.updated_by = value, row.version + 1, now, actor
        db.add(SettingAudit(key=key, old_value_json=old, new_value_json=value, at=now, actor=actor))
    await db.commit()


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_end(now: datetime | None = None) -> datetime:
    """The first instant of the next calendar month (UTC), the exclusive end of the month `now` falls in."""
    start = month_start(now)
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


def _counted():
    return func.coalesce(SpendLedger.settled_cents, SpendLedger.cap_cents)


async def run_spent(db, run_key: str) -> int:
    total = await db.scalar(select(func.coalesce(func.sum(_counted()), 0)).where(
        SpendLedger.run_key == run_key, SpendLedger.status.in_(("reserved", "settled"))))
    return int(total or 0)


async def month_spent(db, owner_id: str, agent_id: int, now: datetime | None = None) -> int:
    total = await db.scalar(select(func.coalesce(func.sum(_counted()), 0)).where(
        SpendLedger.owner_id == owner_id, SpendLedger.agent_id == agent_id,
        SpendLedger.status.in_(("reserved", "settled")), SpendLedger.created_at >= month_start(now),
        SpendLedger.created_at < month_end(now)))
    return int(total or 0)


async def reserve(db, *, owner_id: str, agent_id: int, run_key: str, routine_run_id: int | None, tool: str,
                  cap_cents: int | None) -> tuple[SpendLedger, str | None]:
    """Record a call against the limits. Returns the ledger row and a refusal reason (None when it may go out).

    A refusal is recorded too, so a run can show what stopped it. The caller commits.

    The check and the insert are one step. On SQLite the write transaction taken here holds the database's write lock
    until the caller commits, so a second call waits and then sees this one. On Postgres the worker's row is locked
    for the same span. Without that, two calls could each read the totals before either was recorded and both pass."""
    await begin_write(db)
    await db.execute(select(Agent.id).where(Agent.id == agent_id).with_for_update())
    reason = None
    if cap_cents is None or cap_cents <= 0:
        reason = "no_cap"
    else:
        current = {name: cents for name, (cents, _source) in (await effective_limits(db)).items()}
        if cap_cents > current["per_call"]:
            reason = "per_call"
        elif await run_spent(db, run_key) + cap_cents > current["per_run"]:
            reason = "per_run"
        elif await month_spent(db, owner_id, agent_id) + cap_cents > current["monthly"]:
            reason = "monthly"
    row = SpendLedger(owner_id=owner_id, agent_id=agent_id, run_key=run_key, routine_run_id=routine_run_id, tool=tool,
                      status="refused" if reason else "reserved", cap_cents=cap_cents or 0, reason=reason)
    db.add(row)
    await db.flush()
    return row, reason


def _find_cost(result) -> int | None:
    """The cost a service reports for a finished call, in cents, or None when it reports none."""
    if isinstance(result, dict):
        for key in ("estimated_cost_usd", "cost_usd", "charged_usd"):
            if key in result:
                found = to_cents(result[key])
                if found is not None:
                    return found
        for value in result.values():
            if isinstance(value, (dict, list)):
                found = _find_cost(value)
                if found is not None:
                    return found
    elif isinstance(result, list):
        for value in result:
            found = _find_cost(value)
            if found is not None:
                return found
    return None


def reported_cost(result) -> int | None:
    """Cost in cents from structured content, or from a JSON text block of a tool result."""
    found = _find_cost(result.get("structuredContent")) if isinstance(result, dict) else None
    if found is not None or not isinstance(result, dict):
        return found
    for item in result.get("content", []) if isinstance(result.get("content"), list) else []:
        text = item.get("text") if isinstance(item, dict) else None
        if isinstance(text, str) and text.lstrip().startswith("{"):
            try:
                found = _find_cost(json.loads(text))
            except ValueError:
                continue
            if found is not None:
                return found
    return None


async def settle(db, ledger_id: int, cost_cents: int | None, reason: str | None = None) -> None:
    row = await db.get(SpendLedger, ledger_id)
    if row is None or row.status != "reserved":
        return
    row.status, row.settled_at = "settled", datetime.now(UTC)
    # No reported cost: keep the reserved cap, the safe side of an unknown charge.
    row.settled_cents = row.cap_cents if cost_cents is None else cost_cents
    # Why a call was counted at its cap (or at a reported cost), kept on the row. A refusal's reason is separate.
    row.reason = reason or ("no_cost_reported" if cost_cents is None else None)


async def release(db, ledger_id: int, reason: str | None = None) -> None:
    row = await db.get(SpendLedger, ledger_id)
    if row is not None and row.status == "reserved":
        row.status, row.settled_at, row.settled_cents, row.reason = "released", datetime.now(UTC), 0, reason


def failed_before_running(error: Exception) -> bool:
    """True when a failed call clearly never ran: a refused or rejected request, or no connection was made."""
    return bool(getattr(error, "not_run", False) or getattr(error, "code", None) in NOT_RUN_CODES)


def failure_reason(error: Exception) -> str:
    return f"{'not_run' if failed_before_running(error) else 'unconfirmed'}:{getattr(error, 'code', 'error')}"


async def run_summary(db, run_key: str) -> dict | None:
    rows = (await db.scalars(select(SpendLedger).where(SpendLedger.run_key == run_key).order_by(SpendLedger.id))).all()
    if not rows:
        return None
    spent = sum((r.settled_cents if r.settled_cents is not None else r.cap_cents)
                for r in rows if r.status in ("reserved", "settled"))
    return {"cents": int(spent), "calls": sum(1 for r in rows if r.status in ("reserved", "settled")),
            "refused": [r.reason for r in rows if r.status == "refused" and r.reason]}


async def worker_month(db, owner_id: str, agent_id: int) -> dict:
    return {"agent_id": agent_id, "month_cents": await month_spent(db, owner_id, agent_id)}


async def pause_for_month_limit(db, routine: Routine) -> None:
    routine.paused, routine.paused_reason, routine.next_run_at = True, PAUSE_REASON, None


async def scheduled_paid_use(db, agent_id: int) -> bool:
    agent = await db.get(Agent, agent_id)
    return bool(agent is not None and agent.scheduled_paid_use)
