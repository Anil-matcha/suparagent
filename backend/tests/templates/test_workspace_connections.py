"""Per-workspace readiness of the connections a template names (`requires`)."""

import dataclasses
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.core import crypto
from app.core.config import settings
from app.db.models import CommunityMcpConnection, UserCredential
from app.db.session import get_db
from app.routers.templates import router as templates_router
from app.services import workspace_connections
from app.services.thread_runtime import LOCAL_OWNER_ID
from app.templates import catalog

MUAPI_MANIFEST = {"slug": "muapi", "name": "Muapi", "endpoint": "https://api.muapi.ai/mcp", "auth_mode": "bearer_token"}


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


DATA_KEY = "integration:muapi:data-api"


async def install(db, token="mu_secret_token"):
    """Save the MuAPI data-API key, which is what makes MuAPI ready."""
    if token is not None:
        db.add(UserCredential(
            user_id=LOCAL_OWNER_ID, provider=DATA_KEY,
            file_path="-", ciphertext=crypto.encrypt(json.dumps(token)),
        ))
    await db.flush()


async def test_nothing_is_ready_in_an_empty_workspace(db_session):
    assert await workspace_connections.ready_connections(db_session) == frozenset()


async def test_muapi_is_ready_once_the_data_key_is_saved(db_session):
    await install(db_session)
    assert await workspace_connections.ready_connections(db_session) == frozenset({"muapi"})


async def test_no_saved_key_is_not_ready(db_session):
    await install(db_session, token=None)
    assert await workspace_connections.muapi_ready(db_session) is False


async def test_a_marketplace_listing_and_its_token_without_the_data_key_are_not_ready(db_session):
    db_session.add(CommunityMcpConnection(
        id=str(uuid.uuid4()), owner_id=LOCAL_OWNER_ID, slug="muapi", section="community",
        manifest=MUAPI_MANIFEST, manifest_digest="d" * 64,
    ))
    db_session.add(UserCredential(
        user_id=LOCAL_OWNER_ID, provider="integration:community-mcp:muapi",
        file_path="-", ciphertext=crypto.encrypt(json.dumps("mu_secret_token")),
    ))
    await db_session.flush()
    assert await workspace_connections.muapi_ready(db_session) is False


async def test_an_undecryptable_key_is_not_ready(db_session):
    db_session.add(UserCredential(
        user_id=LOCAL_OWNER_ID, provider=DATA_KEY, file_path="-", ciphertext="not-a-valid-ciphertext",
    ))
    await db_session.flush()
    assert await workspace_connections.muapi_ready(db_session) is False


async def test_the_muapi_computer_key_does_not_count(db_session):
    db_session.add(UserCredential(
        user_id=LOCAL_OWNER_ID, provider="muapi", file_path="-", ciphertext=crypto.encrypt(json.dumps("computer_key")),
    ))
    await db_session.flush()
    assert await workspace_connections.muapi_ready(db_session) is False


def needs_nothing(monkeypatch):
    # Every bundled worker needs MuAPI now, so a copy with no requirement stands in for a ready role.
    template = dataclasses.replace(catalog.get("web-web-developer"), id="web-needs-nothing", requires=())
    add_template(monkeypatch, template)
    return template


def needs_muapi(monkeypatch):
    template = dataclasses.replace(catalog.get("web-web-developer"), id="web-needs-muapi", requires=("muapi",))
    add_template(monkeypatch, template)
    return template


def add_template(monkeypatch, template):
    monkeypatch.setattr(catalog, "TEMPLATES", catalog.TEMPLATES + (template,))
    monkeypatch.setattr(catalog, "_BY_ID", {**catalog._BY_ID, template.id: template})


def needs_email(monkeypatch):
    template = dataclasses.replace(catalog.get("web-web-developer"), id="web-needs-email", requires=("email",))
    add_template(monkeypatch, template)
    return template


def test_a_role_that_needs_only_muapi_can_be_added_before_it_is_connected(monkeypatch):
    template = needs_muapi(monkeypatch)
    assert template.is_available(frozenset())
    assert template.missing(frozenset()) == ("muapi",)
    assert template.missing(frozenset({"muapi"})) == ()


def test_a_role_that_needs_an_unconnectable_app_stays_unavailable(monkeypatch):
    mailroom = needs_email(monkeypatch)
    assert not mailroom.is_available(frozenset())
    assert mailroom.missing(frozenset()) == ("email",)
    assert not mailroom.is_available(frozenset({"muapi"}))
    assert needs_nothing(monkeypatch).missing(frozenset()) == ()


def test_ready_roles_sort_before_roles_that_still_need_a_connection(monkeypatch):
    ready = needs_nothing(monkeypatch)
    template = needs_muapi(monkeypatch)
    mailroom = needs_email(monkeypatch)
    ids = [t.id for t in catalog.search(None, None, frozenset())]
    assert ids.index(ready.id) < ids.index(template.id) < ids.index(mailroom.id)
    ids_ready = [t.id for t in catalog.search(None, None, frozenset({"muapi"}))]
    assert ids_ready.index(template.id) < ids_ready.index(mailroom.id)


def client(db):
    app = FastAPI()
    app.include_router(templates_router, prefix="/api/v1")

    async def override():
        yield db

    app.dependency_overrides[get_db] = override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app, client=("127.0.0.1", 50000)), base_url="http://localhost")


async def test_the_list_route_reports_missing_connections_per_workspace(db_session, monkeypatch):
    ready = needs_nothing(monkeypatch)
    template = needs_muapi(monkeypatch)
    mailroom = needs_email(monkeypatch)
    async with client(db_session) as http:
        body = (await http.get("/api/v1/templates")).json()
        by_id = {t["id"]: t for t in body["templates"]}
        assert by_id[template.id]["available"] is True
        assert by_id[template.id]["missing"] == ["muapi"]
        assert by_id[mailroom.id]["available"] is False
        assert by_id[mailroom.id]["missing"] == ["email"]
        assert by_id[ready.id]["missing"] == []
        assert by_id["web-web-developer"]["missing"] == ["muapi"]

        await install(db_session)
        body = (await http.get("/api/v1/templates")).json()
        assert {t["id"]: t for t in body["templates"]}[template.id]["missing"] == []


async def test_the_single_template_route_reports_missing_connections(db_session, monkeypatch):
    template = needs_muapi(monkeypatch)
    async with client(db_session) as http:
        found = (await http.get(f"/api/v1/templates/{template.id}")).json()
        assert found["available"] is True and found["missing"] == ["muapi"]
        assert (await http.get("/api/v1/templates/web-web-developer")).json()["missing"] == ["muapi"]
        assert (await http.get("/api/v1/templates/nope-missing")).status_code == 404
