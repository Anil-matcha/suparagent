"""POST /hooks/composio/{route_id} (BL-177): signed by Composio, answered 200 once accepted, one 401 for everything else.

The module that owns the saved route and secret (app/services/app_events.py) is built separately, so these tests
replace it with a stub that has the same two functions. The signature scheme and envelope are ASSUMED (see
app/composio_events.py); the fixtures here are UNVERIFIED against a real delivery."""
import base64
import hashlib
import hmac
import json
import logging
import sys
import time
import types

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.auth.policy import is_public
from app.core.config import settings
from app.db.models import AppEventDay, RoutineRun
from app.routers.composio_hooks import MAX_BODY_BYTES, router as hooks_router
from app.services import routine_service as service
from tests.services.test_accept_app_event import (  # noqa: F401
    ACCOUNT, SEED, TEXT, TRIGGER, app_event_cleanup, hexid, mail_routine, share)
from tests.services.test_routine_service import (  # noqa: F401
    T0, agent, agent_args, clock, local_enabled, routine_cleanup, runs_of, scheduler)
from tests.services.thread_harness import cleanup, cli, owner, rt, sessions, wait_until  # noqa: F401

ROUTE = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
SECRET = "s" * 64


@pytest.fixture(autouse=True)
def saved_route_and_secret(monkeypatch):
    stub = types.ModuleType("app.services.app_events")

    async def route(db, owner_id):
        return stub.route

    async def secret(db, owner_id):
        return stub.secret

    stub.route, stub.secret = ROUTE, SECRET
    stub.webhook_route_for, stub.webhook_secret_for = route, secret
    monkeypatch.setitem(sys.modules, "app.services.app_events", stub)
    return stub


@pytest.fixture
def api(scheduler):
    app = FastAPI()
    app.include_router(hooks_router)
    app.dependency_overrides[service.get_scheduler] = lambda: scheduler
    app.state.routines_run_here = False
    app.state.scheduler = scheduler
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app, client=("203.0.113.9", 50000)), base_url="http://hooks.example")
    client.app = app
    return client


def envelope(delivery_id="evt_1", thread="aa11", **data):
    return {"id": delivery_id, "type": "composio.trigger.message",
            "metadata": {"trigger_id": TRIGGER, "connected_account_id": ACCOUNT, "user_id": "u1"},
            "data": {"thread_id": thread, "message_id": "bb22", "message_text": TEXT, "sender": "x@example.com", **data}}


def signed(payload, *, secret=SECRET, delivery_id=None, timestamp=None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    delivery_id = delivery_id or (payload.get("id", "evt_1") if isinstance(payload, dict) else "evt_1")
    timestamp = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{delivery_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return body, {"webhook-id": delivery_id, "webhook-timestamp": str(timestamp), "webhook-signature": "v1," + base64.b64encode(mac).decode(),
                  "content-type": "application/json"}


async def post(api, payload, route=ROUTE, **kw):
    body, headers = signed(payload, **kw)
    return await api.post(f"/hooks/composio/{route}", content=body, headers=headers)


# -- accepted ---------------------------------------------------------------------------------------------------------

async def test_a_signed_new_mail_event_is_accepted_and_queues_one_run(api, sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    response = await post(api, envelope())
    assert response.status_code == 200 and response.json() == {"received": True}
    assert response.headers["cache-control"] == "no-store"
    (run,) = await runs_of(sessions, routine.id)
    assert run.trigger == "app_event" and run.state == "queued" and "aa11" in run.prompt
    assert TEXT not in run.prompt


async def test_a_replayed_delivery_is_answered_200_and_never_processed_twice(api, sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    body, headers = signed(envelope())
    for _ in range(2):
        assert (await api.post(f"/hooks/composio/{ROUTE}", content=body, headers=headers)).status_code == 200
    assert len(await runs_of(sessions, routine.id)) == 1
    async with sessions() as db:
        assert (await db.execute(select(AppEventDay.duplicate))).scalar_one() == 1


async def test_events_that_are_dropped_on_purpose_are_still_answered_200(api, sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock, shares=share(5))
    from app import gmail_share
    outside = hexid(gmail_share.rule_from_json(share(5)["gmail"][ACCOUNT]), False)
    unknown = envelope("evt_2")
    unknown["metadata"]["trigger_id"] = "ti_unknown"
    for n, payload in enumerate([envelope("evt_3", thread=outside), unknown, {"id": "evt_4", "type": "composio.something.else"},
                                 envelope("evt_5", thread="not hex!")]):
        assert (await post(api, payload)).status_code == 200, n
    assert await runs_of(sessions, routine.id) == []


async def test_a_signed_body_that_is_not_json_or_not_the_signed_delivery_is_ignored_with_a_200(api, sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    assert (await post(api, b"not json at all", delivery_id="evt_1")).status_code == 200
    assert (await post(api, envelope("evt_1"), delivery_id="evt_other")).status_code == 200   # body id differs from webhook-id
    assert await runs_of(sessions, routine.id) == []


async def test_the_body_is_never_logged(api, sessions, owner, agent, clock, caplog):
    await mail_routine(sessions, owner, agent, clock)
    with caplog.at_level(logging.DEBUG):
        await post(api, envelope())
        await post(api, b"{" + TEXT.encode())
    assert TEXT not in caplog.text and SECRET not in caplog.text


async def test_the_scheduler_is_woken_only_where_routines_run(api, sessions, owner, agent, clock, scheduler, monkeypatch):
    kicks = []
    monkeypatch.setattr(scheduler, "kick", lambda: kicks.append(1))
    await mail_routine(sessions, owner, agent, clock)
    await post(api, envelope("evt_1"))
    assert kicks == []                      # the ingress process only queues
    api.app.state.routines_run_here = True
    await post(api, envelope("evt_2", thread="cc33"))
    assert kicks == [1]


# -- refused: one 401, no detail --------------------------------------------------------------------------------------

def refusal_cases(api):
    good_body, good = signed(envelope())
    stale_body, stale = signed(envelope(), timestamp=int(time.time()) - 3600)
    other_body, other = signed(envelope(), secret="another-secret")
    big_body, big = signed(b" " * (MAX_BODY_BYTES + 1), delivery_id="evt_1")
    return {
        "wrong route": (ROUTE[::-1], good_body, good),
        "malformed route": ("not-a-route-id", good_body, good),
        "wrong signature": (ROUTE, good_body, other),
        "stale timestamp": (ROUTE, stale_body, stale),
        "missing signature headers": (ROUTE, good_body, {"content-type": "application/json"}),
        "tampered body": (ROUTE, good_body + b" ", good),
        "oversized body": (ROUTE, big_body, big),
    }


async def test_every_refusal_is_the_same_401_with_no_detail(api, sessions, owner, agent, clock):
    routine = await mail_routine(sessions, owner, agent, clock)
    seen = set()
    for name, (route, body, headers) in refusal_cases(api).items():
        response = await api.post(f"/hooks/composio/{route}", content=body, headers=headers)
        assert response.status_code == 401, name
        seen.add((response.text, response.headers.get("cache-control")))
    assert len(seen) == 1                                    # nothing says which part was wrong
    assert await runs_of(sessions, routine.id) == []


async def test_with_no_secret_or_route_saved_the_answer_is_the_same_401(api, saved_route_and_secret, sessions, owner, agent, clock):
    await mail_routine(sessions, owner, agent, clock)
    reference = (await api.post(f"/hooks/composio/{ROUTE[::-1]}", content=b"{}")).text
    for route, secret in ((ROUTE, None), (None, SECRET), (None, None)):
        saved = saved_route_and_secret
        saved.route, saved.secret = route, secret
        response = await post(api, envelope())
        assert response.status_code == 401 and response.text == reference


async def test_the_hosted_account_route_is_absent_unless_tenants_are_on_and_is_a_401_for_a_bad_account(api, monkeypatch):
    body, headers = signed(envelope())
    monkeypatch.setattr(settings, "TENANT_SCHEMAS_ENABLED", False)
    assert (await api.post(f"/hooks/composio/t/{'0' * 32}/{ROUTE}", content=body, headers=headers)).status_code == 404
    assert (await api.post(f"/hooks/t/{'0' * 32}/composio/{ROUTE}", content=body, headers=headers)).status_code == 404
    monkeypatch.setattr(settings, "TENANT_SCHEMAS_ENABLED", True)
    for tenant in ("zz", "0" * 32):
        assert (await api.post(f"/hooks/composio/t/{tenant}/{ROUTE}", content=body, headers=headers)).status_code == 401
        # the address the trigger setup registers (like the routine webhooks) behaves the same
        assert (await api.post(f"/hooks/t/{tenant}/composio/{ROUTE}", content=body, headers=headers)).status_code == 401


# -- registration and the access policy ------------------------------------------------------------------------------

def test_the_route_needs_no_local_access_token_because_the_signature_is_the_credential():
    assert is_public("POST", f"/hooks/composio/{ROUTE}")
    assert is_public("POST", f"/hooks/composio/t/{'0' * 32}/{ROUTE}")
    assert is_public("POST", f"/hooks/t/{'0' * 32}/composio/{ROUTE}")
    for method, path in [("GET", f"/hooks/composio/{ROUTE}"), ("POST", "/hooks/composio/short"), ("POST", f"/hooks/composio/{ROUTE.upper()}"),
                         ("POST", f"/hooks/composio/{ROUTE}/extra"), ("DELETE", f"/hooks/composio/{ROUTE}")]:
        assert not is_public(method, path), (method, path)


def test_the_route_is_served_by_the_app_and_by_the_ingress():
    from app.ingress import app as ingress
    from app.main import app

    for application in (app, ingress):
        paths = set()
        for route in application.routes:  # an included router is nested in this FastAPI version
            paths |= {getattr(inner, "path", None) for inner in getattr(getattr(route, "original_router", None), "routes", [])}
            paths.add(getattr(route, "path", None))
        assert "/hooks/composio/{route_id}" in paths and "/hooks/composio/t/{tenant_hex}/{route_id}" in paths
        assert "/hooks/t/{tenant_hex}/composio/{route_id}" in paths
