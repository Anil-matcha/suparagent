"""A member's own password, handing the workspace to another owner, and open event streams
ending when a sign-in stops being valid."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from sqlalchemy import update

from app.auth import live, people
from app.auth.middleware import AuthMiddleware
from app.auth.people_routes import join_router, router as people_router
from app.auth.routes import router as auth_router
from app.db.models.auth import AuthSession
from app.db.session import get_db
from tests.auth.test_auth_api import PASSPHRASE, app, auth_settings, client, error_code, factory  # noqa: F401
from tests.auth.test_people import PASSWORD, clean_people, member, owner_api, write  # noqa: F401

OWNER_PASSWORD = "owner password 123"


# --- passwords ----------------------------------------------------------------


async def test_a_member_changes_their_password_and_their_other_sessions_end(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    first, session = await member(app, api, csrf)
    second = client(app)
    await second.post("/api/v1/auth/people/login", json={"email": "sam@example.com", "password": PASSWORD})
    assert (await second.get("/api/v1/people")).status_code == 200
    mine = write(session["csrf_token"])
    wrong = await first.post("/api/v1/people/me/password", headers=mine,
                             json={"current_password": "not it at all", "new_password": "a new password 789"})
    assert wrong.status_code == 401 and error_code(wrong) == "wrong_password"
    weak = await first.post("/api/v1/people/me/password", headers=mine,
                            json={"current_password": PASSWORD, "new_password": "short"})
    assert weak.status_code == 422 and error_code(weak) == "weak_password"
    done = await first.post("/api/v1/people/me/password", headers=mine,
                            json={"current_password": PASSWORD, "new_password": "a new password 789"})
    assert done.status_code == 204
    assert (await first.get("/api/v1/people")).status_code == 200  # the session that changed it stays
    assert (await second.get("/api/v1/people")).status_code == 401
    fresh = client(app)
    old = await fresh.post("/api/v1/auth/people/login", json={"email": "sam@example.com", "password": PASSWORD})
    assert old.status_code == 401
    new = await fresh.post("/api/v1/auth/people/login",
                           json={"email": "sam@example.com", "password": "a new password 789"})
    assert new.status_code == 200


async def test_the_owner_sets_an_email_and_password_once_with_the_root_passphrase(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    body = {"new_password": OWNER_PASSWORD, "email": "Owner@Example.com"}
    refused = await api.post("/api/v1/people/me/password", json=body, headers=write(csrf))
    assert refused.status_code == 403 and error_code(refused) == "passphrase_required"
    wrong = await api.post("/api/v1/people/me/password", headers=write(csrf),
                           json={**body, "passphrase": "not the passphrase"})
    assert wrong.status_code == 401 and error_code(wrong) == "wrong_passphrase"
    ok = await api.post("/api/v1/people/me/password", json={**body, "passphrase": PASSPHRASE}, headers=write(csrf))
    assert ok.status_code == 204
    signed_in = await client(app).post("/api/v1/auth/people/login",
                                       json={"email": "owner@example.com", "password": OWNER_PASSWORD})
    assert signed_in.status_code == 200 and signed_in.json()["role"] == "owner"
    # Now it behaves like any password: the passphrase alone no longer changes it.
    again = await api.post("/api/v1/people/me/password", json={**body, "passphrase": PASSPHRASE}, headers=write(csrf))
    assert again.status_code == 401 and error_code(again) == "wrong_password"


async def test_an_email_already_in_use_cannot_become_the_owners(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    await member(app, api, csrf)
    response = await api.post("/api/v1/people/me/password", headers=write(csrf), json={
        "new_password": OWNER_PASSWORD, "email": "sam@example.com", "passphrase": PASSPHRASE})
    assert response.status_code == 409 and error_code(response) == "email_taken"


# --- ownership ----------------------------------------------------------------


async def give_owner_credentials(api, csrf, email="owner@example.com"):
    response = await api.post("/api/v1/people/me/password", headers=write(csrf), json={
        "new_password": OWNER_PASSWORD, "email": email, "passphrase": PASSPHRASE})
    assert response.status_code == 204, response.text


async def test_the_owner_hands_the_workspace_to_a_member(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    await give_owner_credentials(api, csrf)
    sam_api, sam = await member(app, api, csrf)
    done = await api.post("/api/v1/people/transfer", headers=write(csrf),
                          json={"person_id": sam["person_id"], "passphrase": PASSPHRASE})
    assert done.status_code == 200 and done.json()["role"] == "owner"
    # Sam's existing session is the owner's now; the old owner is an admin who signs in by email.
    assert (await sam_api.get("/api/v1/auth/session")).json()["role"] == "owner"
    assert (await api.get("/api/v1/auth/session")).status_code == 401  # their passphrase session ended
    back = await client(app).post("/api/v1/auth/people/login",
                                  json={"email": "owner@example.com", "password": OWNER_PASSWORD})
    assert back.status_code == 200 and back.json()["role"] == "admin"
    # The old passphrase is dead, so it cannot sign in as the new owner.
    stale = await client(app).post("/api/v1/auth/login", json={"passphrase": PASSPHRASE})
    assert stale.status_code == 401
    # Exactly one owner, and the change is in the audit log.
    roster = (await sam_api.get("/api/v1/people")).json()
    assert [p["role"] for p in roster].count("owner") == 1
    actions = [r["action"] for r in (await sam_api.get("/api/v1/people/audit")).json()]
    assert "ownership.transferred" in actions
    # The new owner can do owner-only things now.
    invited = await sam_api.post("/api/v1/people/invites", headers=write(sam["csrf_token"]),
                                 json={"email": "ad@example.com", "role": "admin"})
    assert invited.status_code == 201


async def test_a_transfer_needs_the_passphrase_and_both_people_able_to_sign_in(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    _, sam = await member(app, api, csrf)
    payload = {"person_id": sam["person_id"], "passphrase": PASSPHRASE}
    # The owner has no email and password yet, so a handover would lock them out.
    early = await api.post("/api/v1/people/transfer", json=payload, headers=write(csrf))
    assert early.status_code == 409 and error_code(early) == "owner_needs_password"
    await give_owner_credentials(api, csrf)
    wrong = await api.post("/api/v1/people/transfer", headers=write(csrf),
                           json={**payload, "passphrase": "not the passphrase"})
    assert wrong.status_code == 401 and error_code(wrong) == "wrong_passphrase"
    missing = await api.post("/api/v1/people/transfer", json={**payload, "person_id": 999999}, headers=write(csrf))
    assert missing.status_code == 404
    me = (await api.get("/api/v1/auth/session")).json()["person_id"]
    same = await api.post("/api/v1/people/transfer", json={**payload, "person_id": me}, headers=write(csrf))
    assert same.status_code == 409
    # Nothing changed: the passphrase still works.
    assert (await api.get("/api/v1/auth/session")).json()["role"] == "owner"
    assert (await client(app).post("/api/v1/auth/login", json={"passphrase": PASSPHRASE})).status_code == 200


async def test_only_the_owner_can_transfer(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    admin_api, admin = await member(app, api, csrf, "ann@example.com", role="admin")
    _, sam = await member(app, api, csrf, "sam@example.com")
    response = await admin_api.post("/api/v1/people/transfer", headers=write(admin["csrf_token"]),
                                    json={"person_id": sam["person_id"], "passphrase": PASSPHRASE})
    assert response.status_code == 403 and error_code(response) == "owner_only"


# --- open streams -------------------------------------------------------------


@pytest.fixture
def stream_app(factory):  # noqa: F811
    """The real auth and people routes plus a stand-in event stream at a real stream path."""
    application = FastAPI()
    for router in (auth_router, join_router, people_router):
        application.include_router(router, prefix="/api/v1")

    @application.get("/api/v1/threads/1/events/stream")
    async def stream():
        async def frames():
            yield "data: hello\n\n"
            while True:
                await asyncio.sleep(0.05)

        return StreamingResponse(frames(), media_type="text/event-stream")

    application.add_middleware(AuthMiddleware, session_factory=factory)

    async def database():
        async with factory() as db:
            yield db

    application.dependency_overrides[get_db] = database
    return application


async def open_stream(api):
    """A request that only returns once the server ends the stream."""
    task = asyncio.ensure_future(api.get("/api/v1/threads/1/events/stream"))
    await asyncio.sleep(0.5)
    assert not task.done(), "the stream should still be open"
    return task


async def test_removing_a_member_ends_their_open_event_stream(stream_app, factory):  # noqa: F811
    api, csrf = await owner_api(stream_app, factory)
    joined, _ = await member(stream_app, api, csrf)
    watcher = await open_stream(joined)
    owner_watcher = await open_stream(api)
    sam = next(p for p in (await api.get("/api/v1/people")).json() if p["display_name"] == "Sam")
    assert (await api.delete(f"/api/v1/people/{sam['id']}", headers=write(csrf))).status_code == 204
    response = await asyncio.wait_for(watcher, timeout=5)
    assert response.status_code == 200 and "hello" in response.text
    assert not owner_watcher.done()  # nobody else is affected
    owner_watcher.cancel()


async def test_a_stream_also_ends_when_the_session_was_revoked_elsewhere(stream_app, factory, monkeypatch):  # noqa: F811
    monkeypatch.setattr(live, "RECHECK_SECONDS", 0.1)
    api, csrf = await owner_api(stream_app, factory)
    joined, _ = await member(stream_app, api, csrf)
    watcher = await open_stream(joined)
    # Another worker process revoked it: only the database knows.
    async with factory() as db:
        sam = next(p for p in await people.list_people(db) if p.role == "member")
        await db.execute(update(AuthSession).where(AuthSession.person_id == sam.id).values(revoked_at=people.utcnow()))
        await db.commit()
    response = await asyncio.wait_for(watcher, timeout=5)
    assert response.status_code == 200


async def test_a_revoked_device_stream_ends_immediately(stream_app, factory):  # noqa: F811
    api, csrf = await owner_api(stream_app, factory)
    watcher = await open_stream(api)
    devices = (await api.get("/api/v1/auth/devices")).json()
    current = next(d for d in devices if d["current"])
    # Revoke the only session another way than the middleware's own check: through the store.
    from app.auth import store

    async with factory() as db:
        assert await store.revoke_session(db, current["id"])
    response = await asyncio.wait_for(watcher, timeout=5)
    assert response.status_code == 200
