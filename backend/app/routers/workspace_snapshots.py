"""Undo a conversation's file changes (BL-102). Owner (admin) only: the route is not on the
chat or files allowlists in app/auth/policy.py, so a paired device cannot call it."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.routers.threads import _thread_or_404, get_runtime
from app.services import workspace_snapshots as snaps
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(prefix="/threads", tags=["Workspace undo"], dependencies=[Depends(require_local_access)])


class SnapshotOut(BaseModel):
    id: str
    label: str
    created: float
    files: int


class RestoreOut(BaseModel):
    snapshot: str
    restored: int
    removed: int
    unchanged: int
    undo_snapshot: str | None = None


def _refuse(error: snaps.SnapshotError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error_code": error.code, "message": error.message})


async def _workspace(rt: ThreadRuntime, thread_id: int) -> str:
    workspace = (await _thread_or_404(rt, thread_id)).workspace_path
    if not workspace:
        raise HTTPException(status_code=409, detail={"error_code": "workspace_missing", "message": "This conversation has no workspace."})
    return workspace


@router.get("/{thread_id}/snapshots", response_model=list[SnapshotOut])
async def list_snapshots(thread_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """The workspace's undo points, newest first (one is taken before each turn)."""
    workspace = await _workspace(rt, thread_id)
    return await asyncio.to_thread(snaps.list_snapshots, snaps.store_dir_for(workspace))


@router.post("/{thread_id}/snapshots/{snapshot_id}/restore", response_model=RestoreOut)
async def restore_snapshot(thread_id: int, snapshot_id: str, rt: ThreadRuntime = Depends(get_runtime)):
    """Rewind the workspace to an undo point. Refused while a turn is running."""
    workspace = await _workspace(rt, thread_id)
    if rt.is_running(thread_id) if hasattr(rt, "is_running") else False:
        raise HTTPException(status_code=409, detail={"error_code": "turn_running", "message": "Stop the running task before undoing."})
    try:
        return await asyncio.to_thread(snaps.restore, workspace, snaps.store_dir_for(workspace), snapshot_id)
    except snaps.SnapshotError as error:
        raise _refuse(error) from None
