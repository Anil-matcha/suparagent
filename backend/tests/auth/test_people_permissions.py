"""What a member may do is chosen when they are invited and can be changed later: replying, reading
files, answering commands, saving standing rules, managing agents. Also who answered an approval."""

import pytest
import pytest_asyncio
from sqlalchemy import update

from app.core.config import settings
from app.db.models.approval import Approval
from app.db.models.turn import Turn
from app.services import thread_store
from app.services.thread_runtime import runtime as default_runtime
from tests.auth.test_auth_api import PASSPHRASE, app, auth_settings, client, error_code, factory  # noqa: F401
from tests.auth.test_people import PASSWORD, clean_people, invite, join, member, owner_api, write  # noqa: F401


async def member_with(app, api, csrf, permissions, email="sam@example.com", name="Sam"):  # noqa: F811
    made = await api.post("/api/v1/people/invites", headers=write(csrf),
                          json={"email": email, "role": "member", "permissions": permissions})
    assert made.status_code == 201, made.text
    assert made.json()["permissions"] == [p for p in ("chat", "files", "approve", "rules", "agents") if p in permissions]
    joined, response = await join(app, made.json()["token"], email, name)
    assert response.status_code == 200, response.text
    return joined, response.json()


# --- what an invitation carries ----------------------------------------------


async def test_a_member_without_chosen_permissions_gets_the_usual_set(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    _, session = await member(app, api, csrf)
    assert session["permissions"] == ["chat", "files", "approve", "agents"]
    assert set(session["scopes"]) == {"chat", "files", "manage"}


async def test_reply_only_can_send_but_not_read_files_or_manage_agents(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    joined, session = await member_with(app, api, csrf, ["chat"])
    mine = write(session["csrf_token"])
    assert session["permissions"] == ["chat"] and session["scopes"] == ["chat"]
    # Sending is allowed: a write that only needs chat gets through.
    assert (await joined.patch("/api/v1/people/me", json={"display_name": "Sammy"}, headers=mine)).status_code == 200
    files = await joined.get("/api/v1/threads/1/files")
    assert files.status_code == 403 and error_code(files) == "scope_denied"
    agents = await joined.post("/api/v1/agents", json={"name": "x"}, headers=mine)
    assert agents.status_code == 403 and error_code(agents) == "scope_denied"


async def test_a_member_with_no_chat_permission_is_read_only(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    joined, session = await member_with(app, api, csrf, ["files"])
    blocked = await joined.post("/api/v1/threads", json={}, headers=write(session["csrf_token"]))
    assert blocked.status_code == 403 and error_code(blocked) == "read_only"
    assert (await joined.get("/api/v1/threads/1/files")).status_code != 403  # may read files, though


async def test_files_and_agents_permissions_open_exactly_those_routes(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    joined, session = await member_with(app, api, csrf, ["chat", "files", "agents"])
    assert (await joined.get("/api/v1/threads/1/files")).status_code != 403
    created = await joined.post("/api/v1/agents", json={"name": "x"}, headers=write(session["csrf_token"]))
    assert created.status_code != 403


async def test_unknown_or_misplaced_permissions_are_refused(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    for body, code in (
        ({"email": "a@example.com", "role": "member", "permissions": ["chat", "fly"]}, "permission_invalid"),
        ({"email": "b@example.com", "role": "viewer", "permissions": ["chat"]}, "permissions_not_editable"),
        ({"email": "c@example.com", "role": "admin", "permissions": ["chat"]}, "permissions_not_editable"),
    ):
        response = await api.post("/api/v1/people/invites", json=body, headers=write(csrf))
        assert response.status_code == 422 and error_code(response) == code, (body, response.text)


async def test_only_the_owner_can_grant_standing_rules(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    admin_api, admin = await member(app, api, csrf, "ann@example.com", role="admin")
    ann = write(admin["csrf_token"])
    refused = await admin_api.post("/api/v1/people/invites", headers=ann, json={
        "email": "x@example.com", "role": "member", "permissions": ["chat", "rules"]})
    assert refused.status_code == 403 and error_code(refused) == "owner_only"
    allowed = await admin_api.post("/api/v1/people/invites", headers=ann, json={
        "email": "x@example.com", "role": "member", "permissions": ["chat", "approve"]})
    assert allowed.status_code == 201
    granted = await api.post("/api/v1/people/invites", headers=write(csrf), json={
        "email": "y@example.com", "role": "member", "permissions": ["chat", "approve", "rules"]})
    assert granted.status_code == 201 and "rules" in granted.json()["permissions"]


# --- changing them later -----------------------------------------------------


async def test_changing_permissions_applies_on_the_next_request_and_is_audited(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    joined, session = await member(app, api, csrf)
    pid = session["person_id"]
    assert (await joined.get("/api/v1/threads/1/files")).status_code != 403
    changed = await api.patch(f"/api/v1/people/{pid}", json={"permissions": ["chat"]}, headers=write(csrf))
    assert changed.status_code == 200 and changed.json()["permissions"] == ["chat"]
    assert (await joined.get("/api/v1/threads/1/files")).status_code == 403
    assert (await joined.get("/api/v1/auth/session")).json()["permissions"] == ["chat"]
    rows = (await api.get("/api/v1/people/audit")).json()
    assert rows[0]["action"] == "member.permissions_changed"
    assert rows[0]["detail"] == "chat files approve agents -> chat"


async def test_changing_a_role_resets_permissions_to_that_roles_set(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    _, session = await member_with(app, api, csrf, ["chat"])
    pid = session["person_id"]
    viewer = await api.patch(f"/api/v1/people/{pid}", json={"role": "viewer"}, headers=write(csrf))
    assert viewer.json()["permissions"] == []
    back = await api.patch(f"/api/v1/people/{pid}", json={"role": "member"}, headers=write(csrf))
    assert back.json()["permissions"] == ["chat", "files", "approve", "agents"]
    nothing = await api.patch(f"/api/v1/people/{pid}", json={}, headers=write(csrf))
    assert nothing.status_code == 422 and error_code(nothing) == "nothing_to_change"


async def test_nobody_edits_their_own_permissions_and_admins_cannot_hand_out_rules(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    admin_api, admin = await member(app, api, csrf, "ann@example.com", role="admin")
    _, sam = await member(app, api, csrf, "sam@example.com")
    ann = write(admin["csrf_token"])
    own = await admin_api.patch(f"/api/v1/people/{admin['person_id']}", json={"permissions": ["chat"]}, headers=ann)
    assert own.status_code in (403, 422)
    rules = await admin_api.patch(f"/api/v1/people/{sam['person_id']}", headers=ann,
                                  json={"permissions": ["chat", "rules"]})
    assert rules.status_code == 403 and error_code(rules) == "owner_only"
    assert (await admin_api.patch(f"/api/v1/people/{sam['person_id']}", headers=ann,
                                  json={"permissions": ["chat", "approve"]})).status_code == 200


# --- approvals ---------------------------------------------------------------


@pytest_asyncio.fixture
async def waiting_approval(factory, tmp_path, monkeypatch):  # noqa: F811
    """A running turn that is waiting for someone to answer one tool call."""
    monkeypatch.setattr(settings, "DECISION_LOG_DIR", str(tmp_path / "decisions"), raising=False)
    async with factory() as db:
        thread = await thread_store.create_thread(db, owner_id=default_runtime.owner_id, provider="claude")
        turn = await thread_store.start_turn(db, thread.id, "do it", origin="person")
        approval = await thread_store.create_approval(
            db, thread_id=thread.id, turn_id=turn.id, request_id="r1", tool="Bash", input={"command": "ls"},
            summary="List files", kind="command", owner_token=turn.owner_token)
    try:
        yield thread.id, approval.id
    finally:
        async with factory() as db:
            # A running turn blocks deleting its thread: end it first.
            await db.execute(update(Turn).where(Turn.thread_id == thread.id).values(status="cancelled"))
            await db.commit()
            await thread_store.delete_thread(db, thread.id)


def answer(thread_id, approval_id):
    return f"/api/v1/threads/{thread_id}/approvals/{approval_id}"


async def test_a_member_without_the_approve_permission_cannot_answer_a_command(app, factory, waiting_approval):  # noqa: F811
    thread_id, approval_id = waiting_approval
    api, csrf = await owner_api(app, factory)
    joined, session = await member_with(app, api, csrf, ["chat"])
    refused = await joined.post(answer(thread_id, approval_id), json={"decision": "allow"},
                                headers=write(session["csrf_token"]))
    assert refused.status_code == 403 and error_code(refused) == "permission_denied"
    async with factory() as db:
        assert (await db.get(Approval, approval_id)).status == "pending"


async def test_a_member_who_may_approve_answers_once_and_is_named(app, factory, waiting_approval):  # noqa: F811
    thread_id, approval_id = waiting_approval
    api, csrf = await owner_api(app, factory)
    joined, session = await member_with(app, api, csrf, ["chat", "approve"])
    done = await joined.post(answer(thread_id, approval_id), headers=write(session["csrf_token"]),
                             json={"decision": "allow", "remember": True})
    assert done.status_code == 200, done.text
    body = done.json()
    # "Always allow" needs the rules permission: the answer counts once, no rule is saved.
    assert body["decision"] == "allow" and body.get("rule_id") is None
    assert "owner's permission" in body["remember_error"]
    async with factory() as db:
        row = await db.get(Approval, approval_id)
        assert row.status == "allowed" and row.decided_by_label == "Sam" and row.decided_by_person_id == session["person_id"]
        events = await thread_store.list_events(db, thread_id, after=0, limit=50)
    resolved = next(e for e in events if e.type == "approval.resolved")
    assert resolved.data["person"] == {"id": session["person_id"], "label": "Sam"}


async def test_the_owner_answers_with_a_name_and_may_save_rules(app, factory, waiting_approval):  # noqa: F811
    thread_id, approval_id = waiting_approval
    api, csrf = await owner_api(app, factory)
    done = await api.post(answer(thread_id, approval_id), headers=write(csrf),
                          json={"decision": "deny", "remember": True})
    assert done.status_code == 200 and "owner's permission" not in (done.json().get("remember_error") or "")
    async with factory() as db:
        row = await db.get(Approval, approval_id)
    assert row.status == "denied" and row.decided_by_label == "Owner"


async def test_approvals_list_shows_who_answered(app, factory, waiting_approval):  # noqa: F811
    thread_id, approval_id = waiting_approval
    api, csrf = await owner_api(app, factory)
    await api.post(answer(thread_id, approval_id), headers=write(csrf), json={"decision": "allow"})
    listed = (await api.get(f"/api/v1/threads/{thread_id}/approvals")).json()
    assert listed[0]["decided_by_label"] == "Owner"


async def test_a_member_room_post_is_marked_as_not_the_owners(app, factory, monkeypatch):  # noqa: F811
    """Agent memory treats a room line as the owner's ("You") unless marked otherwise."""
    from app.routers import rooms as rooms_router

    seen = {}

    class FakeRooms:
        async def post(self, room_id, text, *, author=None, source=None, **kw):
            seen["author"], seen["source"] = author, source
            return 1

        async def tick(self):
            return None

    class FakeRuntime:
        rooms = FakeRooms()

    app.dependency_overrides[rooms_router.get_runtime] = lambda: FakeRuntime()
    api, csrf = await owner_api(app, factory)
    joined, session = await member(app, api, csrf)
    mine = write(session["csrf_token"])
    assert (await joined.post("/api/v1/rooms/1/messages", json={"text": "hi"}, headers=mine)).status_code == 201
    assert seen["author"]["owner"] is False and seen["author"]["label"] == "Sam"
    assert (await api.post("/api/v1/rooms/1/messages", json={"text": "hi"}, headers=write(csrf))).status_code == 201
    assert seen["author"]["owner"] is True and seen["author"]["label"] == "Owner"
