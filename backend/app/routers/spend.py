"""Spending limits for paid provider tools, and the per-worker scheduled paid use switch.

Owner only: these are the app's own settings, reached through the local access policy, never through an agent
tool. Hiring, templates and team setup have no route to either the limits or the switch."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_settings
from app.desktop import profile
from app.db.models.agent import Agent
from app.db.session import get_db
from app.routers.agents import get_runtime
from app.services import spend
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(prefix="/spend-limits", tags=["Spending limits"], dependencies=[Depends(require_local_access)])


class LimitsResponse(BaseModel):
    # US cents, so no rounding drifts between what is shown and what is enforced.
    per_call_cents: int
    per_run_cents: int
    per_worker_month_cents: int
    # Where each value comes from: "database" (the owner changed it), "environment" or "default".
    sources: dict[str, str] = {}


class LimitsChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    per_call_cents: int = Field(ge=0, le=100_000)
    per_run_cents: int = Field(ge=0, le=1_000_000)
    per_worker_month_cents: int = Field(ge=0, le=10_000_000)


class WorkerSpend(BaseModel):
    agent_id: int
    name: str
    scheduled_paid_use: bool
    month_cents: int


class SpendResponse(BaseModel):
    limits: LimitsResponse
    workers: list[WorkerSpend]


class WorkerChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scheduled_paid_use: bool


async def _limits(db: AsyncSession) -> LimitsResponse:
    current = await spend.effective_limits(db)
    return LimitsResponse(per_call_cents=current["per_call"][0], per_run_cents=current["per_run"][0],
                          per_worker_month_cents=current["monthly"][0],
                          sources={name: source for name, (_cents, source) in current.items()})


@router.get("", response_model=SpendResponse)
async def read_spend(rt: ThreadRuntime = Depends(get_runtime), db: AsyncSession = Depends(get_db)):
    agents = (await db.scalars(select(Agent).where(Agent.owner_id == rt.owner_id, Agent.archived_at.is_(None))
                               .order_by(Agent.id))).all()
    workers = [WorkerSpend(agent_id=a.id, name=a.name, scheduled_paid_use=a.scheduled_paid_use,
                           month_cents=await spend.month_spent(db, rt.owner_id, a.id)) for a in agents]
    return SpendResponse(limits=await _limits(db), workers=workers)


@router.put("", response_model=LimitsResponse)
async def change_limits(change: LimitsChange, db: AsyncSession = Depends(get_db)):
    if not profile.active():
        # Headless and hosted installs have no desktop settings registry: the owner's choice is stored in the same
        # settings table and read ahead of the environment value. The route still needs the owner's own session.
        try:
            await spend.save_headless_limits(db, {"per_call": change.per_call_cents, "per_run": change.per_run_cents,
                                                  "monthly": change.per_worker_month_cents})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return await _limits(db)
    values = {"SPEND_PER_CALL_CENTS": change.per_call_cents, "SPEND_PER_RUN_CENTS": change.per_run_cents,
              "SPEND_PER_WORKER_MONTH_CENTS": change.per_worker_month_cents}
    try:
        for key, value in values.items():
            current = app_settings.resolve(key, live=True)
            await app_settings.save(db, key, value, current.version)
    except app_settings.VersionConflict:
        raise HTTPException(409, "The limits changed elsewhere. Reload and try again.") from None
    except PermissionError:
        raise HTTPException(409, "Spending limits cannot be changed right now.") from None
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from None
    return await _limits(db)


@router.put("/workers/{agent_id}", response_model=WorkerSpend)
async def change_worker(agent_id: int, change: WorkerChange, rt: ThreadRuntime = Depends(get_runtime),
                        db: AsyncSession = Depends(get_db)):
    """Turn scheduled paid use on or off for one worker. On means scheduled runs may use this worker's paid
    provider tools without waiting for approval, inside the limits above."""
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.owner_id != rt.owner_id or agent.archived_at is not None:
        raise HTTPException(404, "Agent not found.")
    agent.scheduled_paid_use = change.scheduled_paid_use
    await db.commit()
    return WorkerSpend(agent_id=agent.id, name=agent.name, scheduled_paid_use=agent.scheduled_paid_use,
                       month_cents=await spend.month_spent(db, rt.owner_id, agent.id))
