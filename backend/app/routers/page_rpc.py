"""A page calls the connected-app tools it declared, without an engine turn (services/page_connectors.py).

POST /threads/{thread_id}/pages/{page_id}/rpc   a page shown inline in a conversation
POST /saved-apps/{id}/rpc                        a saved app (its pinned or newest version)

Body: {"method": "connectors/list" | "connectors/call", "params": {...}, "engaged": bool, "confirmation": str}.
The declared tools are read again from the stored page on every call; nothing the client says widens them.
Errors carry `{"detail": {"error_code": "...", "message": "..."}}` (plus `summary` and `confirmation` on 409
`needs_approval`)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.auth.middleware import principal_of
from app.auth.policy import has_permission
from app.db.models import Event
from app.routers.saved_apps import NO_STORE, _json
from app.routers.threads import get_runtime
from app.services import page_connectors as svc
from app.services import saved_apps
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(tags=["Page connectors"], dependencies=[Depends(require_local_access)])


class RpcBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["connectors/list", "connectors/call"]
    params: dict | None = None
    engaged: bool = False
    confirmation: str | None = Field(default=None, max_length=200)


def _error(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message, **extra}, headers=NO_STORE)


def _allowed(request: Request) -> None:
    if not has_permission(principal_of(request.scope), "chat"):
        raise _error(403, "forbidden", "You do not have access to apps.")


async def _dispatch(rt: ThreadRuntime, db, body: RpcBody, *, page_key: tuple, thread_id: int | None, uses, agent_id) -> Response:
    grants = await svc.agent_grants(db, rt.owner_id, agent_id)
    try:
        if body.method == "connectors/list":
            return _json(await svc.list_connectors(db, rt.owner_id, grants, uses))
        params = body.params or {}
        if set(params) - {"toolkit", "tool", "arguments", "account"}:
            raise svc.RpcError(422, "bad_arguments", "connectors/call takes toolkit, tool and arguments.")
        toolkit, tool = params.get("toolkit"), params.get("tool")
        if not isinstance(toolkit, str) or not isinstance(tool, str):
            raise svc.RpcError(422, "bad_arguments", "toolkit and tool must be strings.")
        return _json(await svc.call(
            db, owner_id=rt.owner_id, page_key=page_key, thread_id=thread_id, uses=uses, grants=grants, toolkit=toolkit,
            tool=tool, arguments=params.get("arguments"), account=params.get("account"), engaged=body.engaged,
            confirmation=body.confirmation))
    except svc.RpcError as exc:
        raise _error(exc.status, exc.code, exc.message, **exc.extra) from None


@router.post("/threads/{thread_id}/pages/{page_id}/rpc")
async def thread_page_rpc(thread_id: int, page_id: str, body: RpcBody, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _allowed(request)
    if not saved_apps.PAGE_ID_RE.match(page_id):
        raise _error(404, "not_found", "No such page.")
    async with rt.session() as db:
        from app.db.models import Thread

        thread = await db.get(Thread, thread_id)
        if thread is None or thread.owner_id != rt.owner_id:
            raise _error(404, "not_found", "No such conversation.")
        if thread.room_id is not None:
            raise _error(409, "room_thread", "Pages in a room cannot use connected apps.")
        rows = (await db.execute(
            select(Event.data).where(Event.thread_id == thread_id, Event.type == saved_apps.EVENT).order_by(Event.seq.desc())
        )).scalars().all()
        data = next((d for d in rows if isinstance(d, dict) and d.get("page_id") == page_id), None)
        if data is None:
            raise _error(404, "not_found", "No such page in this conversation.")
        return await _dispatch(rt, db, body, page_key=("thread", rt.owner_id, thread_id, page_id), thread_id=thread_id,
                               uses=data.get("uses"), agent_id=thread.agent_id)


@router.post("/saved-apps/{app_id}/rpc")
async def saved_app_rpc(app_id: str, body: RpcBody, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _allowed(request)
    async with rt.session() as db:
        app = await saved_apps.get_app(db, rt.owner_id, app_id)
        if app is None:
            raise _error(404, "not_found", "No such app.")
        version = saved_apps.pick(app, None)
        return await _dispatch(rt, db, body, page_key=("app", rt.owner_id, app.id), thread_id=app.thread_id,
                               uses=version["uses"] if version else [], agent_id=app.agent_id)
