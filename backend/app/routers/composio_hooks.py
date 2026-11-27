"""Composio webhook deliveries: `POST /hooks/composio/{route_id}` (BL-177).

Composio posts a project's trigger events here. Authenticated by the signature Composio puts on every delivery, not by
the local access check, so Composio can reach it through the ingress (app/ingress.py) or a tunnel. The route id is a
random 128-bit id per install, and the signature is checked before the body is parsed:

  - every failure before the signature is accepted (unknown or malformed route, no secret saved, missing or stale
    headers, a wrong signature, a body that is too large) is the same 401 with no detail, so nothing says which part was wrong;
  - an accepted delivery is always answered 200, even when the event is deliberately dropped (an unknown trigger, a
    thread outside the agent's share, a daily cap), so Composio does not retry a storm; only a crash is a non-2xx;
  - the body is never logged and never stored: composio_events.extract keeps ids only.

The relay path for desktops that cannot be reached from outside passes the same already-verified, already-reduced event
to routine_service.accept_app_event, which knows nothing about HTTP.
"""
import hmac
import importlib
import json
import re
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app import composio_events
from app.core import tenant_jobs
from app.core.config import settings
from app.core.logging import get_logger
from app.db import tenant_schema
from app.routers.hooks import _read_limited, _refuse
from app.services import routine_service as service

logger = get_logger(__name__)
router = APIRouter(tags=["Webhooks"])

MAX_BODY_BYTES = 64 * 1024
_ROUTE_ID = re.compile(r"^[0-9a-f]{32}$")


def _unauthorized() -> JSONResponse:
    return _refuse(401, "Unknown webhook or wrong signature")


def _app_events():
    # Looked up when used: the module that owns the saved route and secret is built separately, and tests replace it.
    return importlib.import_module("app.services.app_events")


@router.post("/hooks/composio/{route_id}")
async def receive(route_id: str, request: Request, scheduler: service.RoutineScheduler = Depends(service.get_scheduler)):
    return await _deliver(route_id, request, scheduler)


# Two spellings of the same hosted address: the one the trigger setup registers (like the routine webhooks, /hooks/t/...)
# and the one that groups every Composio route under /hooks/composio.
@router.post("/hooks/t/{tenant_hex}/composio/{route_id}")
@router.post("/hooks/composio/t/{tenant_hex}/{route_id}")
async def receive_for_account(
    tenant_hex: str, route_id: str, request: Request,
    scheduler: service.RoutineScheduler = Depends(service.get_scheduler),
):
    """A hosted deployment's address: it names the account (its schema), because this route is public. A malformed or
    unknown account answers the same 401, and with TENANT_SCHEMAS_ENABLED off the route does not exist."""
    if not settings.TENANT_SCHEMAS_ENABLED:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    if not re.fullmatch(r"[0-9a-f]{32}", tenant_hex):
        return _unauthorized()
    tenant = f"u_{tenant_hex}"
    if tenant not in await tenant_jobs.tenants():
        return _unauthorized()
    with tenant_schema.use_tenant(tenant):
        return await _deliver(route_id, request, scheduler)


async def _deliver(route_id: str, request: Request, scheduler: service.RoutineScheduler):
    if not _ROUTE_ID.match(route_id):
        return _unauthorized()
    owner_id = scheduler.owner_id
    events = _app_events()
    async with scheduler.session() as db:
        saved_route = await events.webhook_route_for(db, owner_id)
        secret = await events.webhook_secret_for(db, owner_id)
    if not saved_route or not secret or not hmac.compare_digest(saved_route.encode(), route_id.encode()):
        return _unauthorized()
    body = await _read_limited(request, MAX_BODY_BYTES)
    if body is None:
        return _unauthorized()
    try:
        delivery_id = composio_events.verify(request.headers, body, secret, time.time())
    except composio_events.SignatureError:
        return _unauthorized()
    # From here the delivery is Composio's own and recent. Replays inside the window are caught by the event id's
    # receipt in accept_app_event (counted as duplicates, never processed twice).
    ok = JSONResponse(status_code=200, content={"received": True}, headers={"Cache-Control": "no-store"})
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ok
    event = composio_events.extract(envelope, delivery_id)
    if event is None:
        return ok  # an event type we do not act on, or one that does not match what was signed
    async with scheduler.session() as db:
        result = await service.accept_app_event(db, route_id, event, owner_id=owner_id)
    # Only the backend that runs routines starts one now; the ingress process just queues.
    if result.get("outcome") in ("queued", "merged") and getattr(request.app.state, "routines_run_here", False):
        scheduler.kick()
    return ok
