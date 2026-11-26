"""The labels a support worker applied in Gmail (BL-173): list them and remove them again in one step.

GET  /api/v1/agents/{id}/worker-labels        what it applied (counts and label names only)
POST /api/v1/agents/{id}/worker-labels/undo   remove exactly those labels from exactly those threads

Only the owner or an admin may use them: undo changes the owner's mailbox. The agent has no tool for either."""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.middleware import principal_of
from app.routers.agents import get_runtime
from app.schemas.worker_labels import WorkerLabelSummary, WorkerLabelUndo
from app.services import worker_labels
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(
    prefix="/agents",
    tags=["Worker labels"],
    dependencies=[Depends(require_local_access)],
)


def _admin_only(request: Request) -> None:
    principal = principal_of(request.scope)
    if principal is not None and not principal.is_admin:  # a paired phone without admin access
        raise HTTPException(status_code=403, detail={
            "error_code": "worker_labels_require_admin",
            "message": "Worker labels can only be viewed and removed from the web app.",
        })


@router.get("/{agent_id}/worker-labels", response_model=WorkerLabelSummary)
async def get_worker_labels(agent_id: int, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _admin_only(request)
    found = await worker_labels.summary(rt, agent_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return found


@router.post("/{agent_id}/worker-labels/undo", response_model=WorkerLabelUndo)
async def undo_worker_labels(agent_id: int, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    """Remove the labels the agent applied. Safe to repeat: a second call only retries what is left. At most 500
    labels and a minute of work per request; call again for the rest."""
    _admin_only(request)
    done = await worker_labels.undo(rt, agent_id)
    if done is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return done
