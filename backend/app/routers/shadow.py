"""Shadow-mode results for one agent (BL-173): GET /api/v1/agents/{id}/shadow-report.

Read-only and deterministic: no model runs, nothing is written to Gmail. The report reads mail through the agent's
own account under its current share of the mailbox, so only the owner or an admin may ask for it."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth.middleware import principal_of
from app.routers.agents import get_runtime
from app.schemas.shadow import ShadowReport
from app.services import shadow_report
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(
    prefix="/agents",
    tags=["Shadow"],
    dependencies=[Depends(require_local_access)],
)


@router.get("/{agent_id}/shadow-report", response_model=ShadowReport)
async def get_shadow_report(
    agent_id: int,
    request: Request,
    days: int = Query(28, ge=1, le=90),
    refresh: bool = False,
    rt: ThreadRuntime = Depends(get_runtime),
):
    """How the worker's predictions compare with what the owner did, over the last `days` days. `refresh` recomputes
    (at most once a minute per agent); otherwise a report under ten minutes old is returned."""
    principal = principal_of(request.scope)
    if principal is not None and not principal.is_admin:  # a paired phone without admin access
        raise HTTPException(status_code=403, detail={
            "error_code": "shadow_report_requires_admin",
            "message": "The shadow-mode results can only be read from the web app.",
        })
    report = await shadow_report.build(rt, agent_id, days, refresh=refresh)
    if report is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return report
