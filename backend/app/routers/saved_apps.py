"""Saved apps: a page kept as an app that outlives its chat.

GET    /saved-apps                  the owner's apps
POST   /saved-apps                  save a page of a conversation (idempotent per page)
GET    /saved-apps/{id}             one app
PATCH  /saved-apps/{id}             rename, or pin a kept version (null follows the newest)
DELETE /saved-apps/{id}
GET    /saved-apps/{id}/document    the page to render (?rev=, default the pinned or newest version)

Errors carry `{"detail": {"error_code": "...", "message": "..."}}`."""
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.auth.middleware import principal_of
from app.auth.policy import has_permission
from app.routers.threads import get_runtime
from app.services import saved_apps as svc
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(prefix="/saved-apps", tags=["Saved apps"], dependencies=[Depends(require_local_access)])
NO_STORE = {"Cache-Control": "no-store"}


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error_code": code, "message": message})


def _allowed(request: Request) -> None:
    if not has_permission(principal_of(request.scope), "chat"):
        raise _error(403, "forbidden", "You do not have access to apps.")


def _raise(exc: svc.SavedAppError):
    raise _error(exc.status, exc.code, exc.message) from None


class SavedAppCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: int = Field(ge=1)
    page_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class SavedAppUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    pinned_rev: int | None = Field(default=None, ge=1)


def _json(payload, status: int = 200) -> Response:
    from fastapi.encoders import jsonable_encoder
    import json
    return Response(json.dumps(jsonable_encoder(payload)), status_code=status, media_type="application/json", headers=NO_STORE)


async def _one(rt: ThreadRuntime, app_id: str):
    async with rt.session() as db:
        app = await svc.get_app(db, rt.owner_id, app_id)
        if app is None:
            raise _error(404, "not_found", "No such app.")
        return app, (await svc.summaries(db, rt.owner_id, [app]))[0]


@router.get("")
async def list_saved_apps(request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _allowed(request)
    async with rt.session() as db:
        apps = await svc.list_apps(db, rt.owner_id)
        return _json({"apps": await svc.summaries(db, rt.owner_id, apps)})


@router.post("")
async def create_saved_app(payload: SavedAppCreate, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _allowed(request)
    async with rt.session() as db:
        try:
            app, created = await svc.create(db, rt.owner_id, payload.thread_id, payload.page_id)
        except svc.SavedAppError as exc:
            _raise(exc)
        item = (await svc.summaries(db, rt.owner_id, [app]))[0]
    return _json({"app": item}, 201 if created else 200)


@router.get("/{app_id}")
async def get_saved_app(app_id: str, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _allowed(request)
    _, item = await _one(rt, app_id)
    return _json({"app": item})


@router.patch("/{app_id}")
async def update_saved_app(app_id: str, payload: SavedAppUpdate, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _allowed(request)
    name = None
    if "name" in payload.model_fields_set:
        name = (payload.name or "").strip()
        if not 1 <= len(name) <= svc.NAME_MAX:
            raise _error(422, "invalid_name", f"The name must be 1 to {svc.NAME_MAX} characters.")
    async with rt.session() as db:
        app = await svc.get_app(db, rt.owner_id, app_id)
        if app is None:
            raise _error(404, "not_found", "No such app.")
        try:
            await svc.update(db, app, name=name, pinned_rev=payload.pinned_rev, set_pin="pinned_rev" in payload.model_fields_set)
        except svc.SavedAppError as exc:
            _raise(exc)
        item = (await svc.summaries(db, rt.owner_id, [app]))[0]
    return _json({"app": item})


@router.delete("/{app_id}")
async def delete_saved_app(app_id: str, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _allowed(request)
    async with rt.session() as db:
        app = await svc.get_app(db, rt.owner_id, app_id)
        if app is None:
            raise _error(404, "not_found", "No such app.")
        await db.delete(app)
        await db.commit()
    return _json({"ok": True})


@router.get("/{app_id}/document")
async def saved_app_document(
    app_id: str, request: Request, rev: str | None = Query(None, pattern=r"^[0-9]{1,6}$"),
    rt: ThreadRuntime = Depends(get_runtime),
):
    _allowed(request)
    app, item = await _one(rt, app_id)
    version = svc.pick(app, int(rev) if rev is not None else None)
    if version is None:
        raise _error(404, "not_found", "No such version.")
    return _json({
        "id": app.id, "name": app.name, "rev": version["rev"], "title": version["title"], "html": version["html"],
        "height": version["height"], "frameless": version["frameless"], "uses": version["uses"],
        "versions": item["versions"], "pinned_rev": app.pinned_rev, "chat": item["chat"],
    })
