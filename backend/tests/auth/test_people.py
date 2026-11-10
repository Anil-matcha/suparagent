"""Human members of one workspace: invitations, roles, removal, audit and attribution,
through the real middleware and routes on the configured database."""

from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from app.auth import people, store
from app.db.models.auth import AuthSession
from app.db.models.people import PeopleAudit, Person, PersonInvite
from tests.auth.test_auth_api import (  # noqa: F401  (fixtures reused: they need no copy here)
    PASSPHRASE,
    app,
    auth_settings,
    client,
    error_code,
    factory,
    login,
    set_owner,
)

PASSWORD = "member password 123"


@pytest_asyncio.fixture(autouse=True)
async def clean_people(factory):  # noqa: F811
    async def wipe():
        async with factory() as db:
            for model in (PeopleAudit, PersonInvite, Person):
                await db.execute(delete(model))
            await db.commit()

    await wipe()
    yield
    await wipe()


async def owner_api(app, factory):  # noqa: F811
    await set_owner(factory)
    api = client(app)
    session = await login(api)
    return api, session["csrf_token"]


def write(csrf):
    return {"X-CSRF-Token": csrf}


async def invite(api, csrf, email, role="member"):
    response = await api.post("/api/v1/people/invites", json={"email": email, "role": role}, headers=write(csrf))
    assert response.status_code == 201, response.text
    return response.json()


async def join(app, token, email, name="Sam", password=PASSWORD):
    api = client(app)
    response = await api.post(
        "/api/v1/auth/invites/accept",
        json={"token": token, "email": email, "display_name": name, "password": password},
    )
    return api, response


async def member(app, api, csrf, email="sam@example.com", role="member", name="Sam"):
    made = await invite(api, csrf, email, role)
    joined, response = await join(app, made["token"], email, name)
    assert response.status_code == 200, response.text
    return joined, response.json()


async def test_the_owner_is_a_person_and_signs_in_as_one(app, factory):
    api, csrf = await owner_api(app, factory)
    session = (await api.get("/api/v1/auth/session")).json()
    assert session["role"] == "owner" and session["person_id"] and session["display_name"] == "Owner"
    roster = (await api.get("/api/v1/people")).json()
    assert [(p["role"], p["is_you"]) for p in roster] == [("owner", True)]


async def test_a_member_joins_with_the_invited_email_and_sees_the_roster_without_addresses(app, factory):
    api, csrf = await owner_api(app, factory)
    joined, session = await member(app, api, csrf)
    assert session["role"] == "member" and session["display_name"] == "Sam"
    assert set(session["scopes"]) == {"chat", "files", "manage"}
    roster = (await joined.get("/api/v1/people")).json()
    assert {p["display_name"] for p in roster} == {"Owner", "Sam"}
    assert all(p["email"] is None for p in roster)
    admin_view = (await api.get("/api/v1/people")).json()
    assert {p["email"] for p in admin_view} == {None, "sam@example.com"}


async def test_a_member_cannot_manage_people_or_devices(app, factory):
    api, csrf = await owner_api(app, factory)
    joined, session = await member(app, api, csrf)
    mine = write(session["csrf_token"])
    response = await joined.post("/api/v1/people/invites", json={"email": "x@example.com"}, headers=mine)
    assert response.status_code == 403 and error_code(response) == "scope_denied"
    assert (await joined.get("/api/v1/people/audit")).status_code == 403
    assert (await joined.get("/api/v1/auth/devices")).status_code == 403
    assert (await joined.delete("/api/v1/people/1", headers=mine)).status_code == 403


async def test_a_wrong_email_fails_closed_and_leaves_the_invitation_usable(app, factory):
    api, csrf = await owner_api(app, factory)
    made = await invite(api, csrf, "sam@example.com")
    _, wrong = await join(app, made["token"], "mallory@example.com")
    assert wrong.status_code == 400 and error_code(wrong) == "invite_invalid"
    _, right = await join(app, made["token"], "SAM@example.com")
    assert right.status_code == 200


async def test_an_invitation_works_once(app, factory):
    api, csrf = await owner_api(app, factory)
    made = await invite(api, csrf, "sam@example.com")
    assert (await join(app, made["token"], "sam@example.com"))[1].status_code == 200
    again = (await join(app, made["token"], "sam@example.com"))[1]
    assert again.status_code == 400 and error_code(again) == "invite_invalid"


async def test_expired_revoked_and_unknown_invitations_are_refused(app, factory):
    api, csrf = await owner_api(app, factory)
    expired = await invite(api, csrf, "old@example.com")
    async with factory() as db:
        await db.execute(update(PersonInvite).values(expires_at=people.utcnow() - timedelta(minutes=1)))
        await db.commit()
    revoked = await invite(api, csrf, "gone@example.com")
    assert (await api.delete(f"/api/v1/people/invites/{revoked['id']}", headers=write(csrf))).status_code == 204
    for token, email in ((expired["token"], "old@example.com"), (revoked["token"], "gone@example.com"),
                         ("not-a-real-token", "sam@example.com")):
        refused = (await join(app, token, email))[1]
        assert refused.status_code == 400 and error_code(refused) == "invite_invalid"


async def test_a_new_invitation_replaces_the_open_one_for_that_address(app, factory):
    api, csrf = await owner_api(app, factory)
    first = await invite(api, csrf, "sam@example.com")
    second = await invite(api, csrf, "sam@example.com", "viewer")
    assert (await join(app, first["token"], "sam@example.com"))[1].status_code == 400
    assert (await join(app, second["token"], "sam@example.com"))[1].json()["role"] == "viewer"


async def test_the_invitation_secret_is_stored_only_as_a_digest(app, factory):
    api, csrf = await owner_api(app, factory)
    made = await invite(api, csrf, "sam@example.com")
    async with factory() as db:
        row = (await db.execute(select(PersonInvite))).scalar_one()
    assert made["token"] not in row.token_hash and len(row.token_hash) == 64
    listed = (await api.get("/api/v1/people/invites")).json()
    assert listed[0]["token"] is None


async def test_a_weak_password_is_refused_without_using_the_invitation(app, factory):
    api, csrf = await owner_api(app, factory)
    made = await invite(api, csrf, "sam@example.com")
    weak = (await join(app, made["token"], "sam@example.com", password="short"))[1]
    assert weak.status_code == 422 and error_code(weak) == "weak_password"
    assert (await join(app, made["token"], "sam@example.com"))[1].status_code == 200


async def test_a_viewer_can_read_but_every_write_is_refused(app, factory):
    api, csrf = await owner_api(app, factory)
    joined, session = await member(app, api, csrf, role="viewer")
    mine = write(session["csrf_token"])
    assert (await joined.get("/api/v1/people")).status_code == 200
    assert (await joined.get("/api/v1/threads")).status_code == 200
    for method, path in (("POST", "/api/v1/threads"), ("PATCH", "/api/v1/people/me"), ("POST", "/api/v1/rooms/1/messages")):
        response = await joined.request(method, path, json={"display_name": "x", "text": "hi"}, headers=mine)
        assert response.status_code == 403 and error_code(response) == "read_only", (method, path, response.text)


async def test_only_the_owner_can_make_or_change_an_admin(app, factory):
    api, csrf = await owner_api(app, factory)
    admin_api, admin_session = await member(app, api, csrf, "ann@example.com", role="admin", name="Ann")
    ann = write(admin_session["csrf_token"])
    # An admin invites members and viewers...
    assert (await admin_api.post("/api/v1/people/invites", json={"email": "v@example.com", "role": "viewer"},
                                 headers=ann)).status_code == 201
    # ...but not another admin.
    refused = await admin_api.post("/api/v1/people/invites", json={"email": "b@example.com", "role": "admin"}, headers=ann)
    assert refused.status_code == 403 and error_code(refused) == "owner_only"
    _, sam = await member(app, api, csrf, "sam@example.com")
    assert (await admin_api.patch(f"/api/v1/people/{sam['person_id']}", json={"role": "admin"},
                                  headers=ann)).status_code == 403
    assert (await api.patch(f"/api/v1/people/{sam['person_id']}", json={"role": "admin"},
                            headers=write(csrf))).status_code == 200
    # An admin cannot touch another admin or the owner.
    owner_id = (await api.get("/api/v1/auth/session")).json()["person_id"]
    for target in (sam["person_id"], owner_id):
        assert (await admin_api.delete(f"/api/v1/people/{target}", headers=ann)).status_code == 403
        assert (await admin_api.patch(f"/api/v1/people/{target}", json={"role": "viewer"},
                                      headers=ann)).status_code == 403


async def test_nobody_can_remove_or_demote_themselves_or_the_owner(app, factory):
    api, csrf = await owner_api(app, factory)
    owner_id = (await api.get("/api/v1/auth/session")).json()["person_id"]
    response = await api.delete(f"/api/v1/people/{owner_id}", headers=write(csrf))
    assert response.status_code == 403 and error_code(response) == "owner_protected"
    admin_api, admin_session = await member(app, api, csrf, "ann@example.com", role="admin")
    mine = write(admin_session["csrf_token"])
    response = await admin_api.delete(f"/api/v1/people/{admin_session['person_id']}", headers=mine)
    assert response.status_code == 403


async def test_a_role_change_applies_on_the_next_request(app, factory):
    api, csrf = await owner_api(app, factory)
    joined, session = await member(app, api, csrf)
    mine = write(session["csrf_token"])
    assert (await joined.patch("/api/v1/people/me", json={"display_name": "Sammy"}, headers=mine)).status_code == 200
    assert (await api.patch(f"/api/v1/people/{session['person_id']}", json={"role": "viewer"},
                            headers=write(csrf))).status_code == 200
    blocked = await joined.patch("/api/v1/people/me", json={"display_name": "Again"}, headers=mine)
    assert blocked.status_code == 403 and error_code(blocked) == "read_only"
    assert (await joined.get("/api/v1/auth/session")).json()["role"] == "viewer"


async def test_removing_a_member_ends_their_session_and_sign_in_but_keeps_history(app, factory):
    api, csrf = await owner_api(app, factory)
    joined, session = await member(app, api, csrf)
    assert (await joined.get("/api/v1/people")).status_code == 200
    assert (await api.delete(f"/api/v1/people/{session['person_id']}", headers=write(csrf))).status_code == 204
    gone = await joined.get("/api/v1/people")
    assert gone.status_code == 401
    again = client(app)
    refused = await again.post("/api/v1/auth/people/login", json={"email": "sam@example.com", "password": PASSWORD})
    assert refused.status_code == 401 and error_code(refused) == "wrong_credentials"
    assert {p["display_name"] for p in (await api.get("/api/v1/people")).json()} == {"Owner"}
    async with factory() as db:
        row = (await db.execute(select(Person).where(Person.email == "sam@example.com"))).scalar_one()
        live = (await db.execute(select(AuthSession).where(AuthSession.person_id == row.id,
                                                           AuthSession.revoked_at.is_(None)))).scalars().all()
    assert row.status == "removed" and row.password_hash is None and not live
    # Re-inviting brings the same person back under the same id.
    back, rejoined = await member(app, api, csrf)
    assert rejoined["person_id"] == session["person_id"]


async def test_members_sign_in_again_with_email_and_password(app, factory):
    api, csrf = await owner_api(app, factory)
    await member(app, api, csrf)
    other = client(app)
    wrong = await other.post("/api/v1/auth/people/login", json={"email": "sam@example.com", "password": "nope nope nope"})
    assert wrong.status_code == 401 and error_code(wrong) == "wrong_credentials"
    unknown = await other.post("/api/v1/auth/people/login", json={"email": "who@example.com", "password": PASSWORD})
    assert unknown.status_code == 401 and error_code(unknown) == "wrong_credentials"
    ok = await other.post("/api/v1/auth/people/login", json={"email": "sam@example.com", "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["role"] == "member"


async def test_wrong_member_passwords_are_locked_out_like_the_owner_passphrase(app, factory):
    api, csrf = await owner_api(app, factory)
    await member(app, api, csrf)
    other = client(app)
    for _ in range(5):
        await other.post("/api/v1/auth/people/login", json={"email": "sam@example.com", "password": "wrong password!"})
    locked = await other.post("/api/v1/auth/people/login", json={"email": "sam@example.com", "password": PASSWORD})
    assert locked.status_code == 429


async def test_changing_the_owner_passphrase_keeps_members_signed_in(app, factory):
    api, csrf = await owner_api(app, factory)
    joined, _ = await member(app, api, csrf)
    async with factory() as db:
        await store.set_passphrase(db, "a different owner passphrase", first_time_only=False)
    assert (await api.get("/api/v1/auth/session")).status_code == 401
    assert (await joined.get("/api/v1/people")).status_code == 200


async def test_every_membership_change_is_in_the_audit_log(app, factory):
    api, csrf = await owner_api(app, factory)
    joined, session = await member(app, api, csrf)
    await api.patch(f"/api/v1/people/{session['person_id']}", json={"role": "viewer"}, headers=write(csrf))
    await api.delete(f"/api/v1/people/{session['person_id']}", headers=write(csrf))
    rows = (await api.get("/api/v1/people/audit")).json()
    assert [r["action"] for r in reversed(rows)] == [
        "invite.created", "member.joined", "member.role_changed", "member.removed"]
    removed = rows[0]
    assert removed["actor"] == "Owner" and removed["subject"] == "sam@example.com"
    assert rows[1]["detail"] == "member -> viewer"


async def test_people_are_not_available_on_the_desktop_app(app, factory, monkeypatch):
    from app.desktop import profile

    api, csrf = await owner_api(app, factory)
    monkeypatch.setattr(profile, "active", lambda: False)
    assert (await api.get("/api/v1/people")).status_code == 200
    from app.auth import people_routes

    monkeypatch.setattr(people_routes.profile, "active", lambda: True)
    response = await api.get("/api/v1/people")
    assert response.status_code == 409 and error_code(response) == "people_unavailable"


async def test_a_turn_records_the_member_who_sent_it(db_session):
    from app.services import thread_store as store_

    thread = await store_.create_thread(db_session, owner_id="owner-1", provider="claude")
    turn = await store_.start_turn(db_session, thread.id, "hello", person_id=7, person_label="Sam")
    assert (turn.person_id, turn.person_label) == (7, "Sam")
