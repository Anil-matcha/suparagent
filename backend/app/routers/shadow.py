"""Shadow-mode results for one agent (BL-173): GET /api/v1/agents/{id}/shadow-report.

Read-only and deterministic: no model runs, nothing is written to Gmail. The report reads mail through the agent's
own account under its current share of the mailbox, so only the owner or an admin may ask for it."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth.middleware import principal_of
from app.routers.agents import get_runtime
from app.schemas.shadow import ShadowFeedbackCounts, ShadowFeedbackIn, ShadowFeedbackOut, ShadowReport, ShareSuggestion
from app.services import share_suggestion, shadow_feedback, shadow_report
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


def _web_only(request: Request, code: str, message: str) -> None:
    principal = principal_of(request.scope)
    if principal is not None and not principal.is_admin:  # a paired phone without admin access
        raise HTTPException(status_code=403, detail={"error_code": code, "message": message})


@router.post("/{agent_id}/shadow-feedback", response_model=ShadowFeedbackOut)
async def give_shadow_feedback(agent_id: int, body: ShadowFeedbackIn, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    """The owner's "that was wrong, and why" about a thread the worker predicted. The thread must have a recorded prediction and
    be inside the agent's current share. Giving feedback again for the same thread and kind replaces the earlier one."""
    _web_only(request, "shadow_feedback_requires_admin", "Shadow-mode feedback can only be given from the web app.")
    async with rt.session() as db:
        try:
            return await shadow_feedback.record(db, rt.owner_id, agent_id, thread_id=body.thread_id, kind=body.kind, reason=body.reason,
                                                note=body.note, account_id=body.account_id)
        except shadow_feedback.FeedbackError as exc:
            raise HTTPException(status_code=exc.status, detail={"error_code": exc.code, "message": exc.message}) from None


@router.get("/{agent_id}/shadow-feedback", response_model=ShadowFeedbackCounts)
async def get_shadow_feedback(agent_id: int, request: Request, days: int = Query(28, ge=1, le=90), rt: ThreadRuntime = Depends(get_runtime)):
    """How much feedback the owner has given in the last `days` days, by reason and kind. Counts only."""
    _web_only(request, "shadow_feedback_requires_admin", "Shadow-mode feedback can only be read from the web app.")
    async with rt.session() as db:
        found = await shadow_feedback.counts(db, rt.owner_id, agent_id, since=datetime.now(UTC) - timedelta(days=days))
    if found is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"agent_id": agent_id, "window_days": days, **found}


@router.get("/{agent_id}/share-suggestion", response_model=ShareSuggestion)
async def get_share_suggestion(agent_id: int, request: Request, refresh: bool = False, rt: ThreadRuntime = Depends(get_runtime)):
    """A proposal to raise or lower the worker's share of the mailbox, from how its drafts were used. It only reads: the share
    changes only when the owner asks the agent update to change it."""
    _web_only(request, "share_suggestion_requires_admin", "The share suggestion can only be read from the web app.")
    found = await share_suggestion.build(rt, agent_id, refresh=refresh)
    if found is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return found
