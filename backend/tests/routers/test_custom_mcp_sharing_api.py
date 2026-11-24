"""Who may see, manage, share and use an owner-added MCP connection in a workspace with other people.

A connection starts private to whoever added it. The workspace owner (no person id) can always switch off
or remove any connection, but only the creator can share one, and nobody can use, test, sign in to or grant
tools from someone else's private connection."""
import pytest
from sqlalchemy import delete, select

from app import custom_mcp_oauth as oauth
from app.db.models import Person, UserCredential
from app.routers import custom_mcp
from tests.routers.test_custom_mcp_api import (  # noqa: F401
    PATH, URL, admin, agent_id, app, auth_settings, client, code_of, factory, login, make, owner, pairing_code,
    ready, server, set_owner, tool,
)
from tests.routers.test_custom_mcp_oauth_api import AUTH, CALLBACK, idp, oauth_connection, start  # noqa: F401


@pytest.fixture
async def people(factory):
    async with factory() as db:
        alice = Person(display_name="Alice", role="admin", status="active")
        bob = Person(display_name="Bob", role="admin", status="active")
        db.add_all([alice, bob])
        await db.commit()
        ids = (alice.id, bob.id)
    yield ids
    async with factory() as db:
        await db.execute(delete(Person).where(Person.id.in_(ids)))
        await db.commit()


@pytest.fixture
async def act(app, admin, people):
    """Switch the identity the next requests act as: None is the workspace owner."""
    who = {"id": None}

    async def bind():
        custom_mcp._actor_var.set(custom_mcp.Actor(person_id=who["id"]))

    app.dependency_overrides[custom_mcp._bind_actor] = bind

    def switch(person_id):
        who["id"] = person_id

    yield switch
    app.dependency_overrides.pop(custom_mcp._bind_actor, None)


async def ids_seen(admin):
    return {c["id"] for c in (await admin.get(f"{PATH}/connections")).json()}


async def test_a_connection_starts_private_to_whoever_added_it(admin, act, people):
    alice, bob = people
    act(alice)
    conn = await make(admin, name="Alice's")
    assert conn["shared"] is False and conn["mine"] is True and conn["can_manage"] is True and conn["added_by"] == "Alice"
    act(bob)
    assert conn["id"] not in await ids_seen(admin)
    act(None)
    seen = {c["id"]: c for c in (await admin.get(f"{PATH}/connections")).json()}
    assert seen[conn["id"]]["mine"] is False and seen[conn["id"]]["can_manage"] is False and seen[conn["id"]]["added_by"] == "Alice"


async def test_someone_elses_private_connection_looks_absent_for_every_action(admin, act, people, server):
    alice, bob = people
    act(alice)
    conn = await make(admin)
    rev = {"revision": conn["revision"]}
    act(bob)
    base = f"{PATH}/connections/{conn['id']}"
    calls = [
        ("patch", base, {"revision": 1, "name": "x"}), ("post", f"{base}/test", rev), ("post", f"{base}/enable", rev),
        ("get", f"{base}/tools", None), ("post", f"{base}/oauth/start", rev), ("post", f"{base}/oauth/sign-out", rev),
        ("delete", f"{base}/credential?revision=1", None), ("post", f"{base}/sharing", {**rev, "shared": True}),
        ("delete", base, None), ("post", f"{base}/disable", rev),
    ]
    for method, url, body in calls:
        response = await (getattr(admin, method)(url, json=body) if body is not None else getattr(admin, method)(url))
        assert response.status_code == 404, (method, url, response.status_code)
    act(alice)
    assert conn["id"] in await ids_seen(admin), "nothing above changed or removed it"


async def test_only_the_creator_can_share_and_sharing_lets_other_admins_manage_it(admin, act, people, server):
    alice, bob = people
    act(alice)
    conn = await make(admin)
    shared = await admin.post(f"{PATH}/connections/{conn['id']}/sharing", json={"revision": conn["revision"], "shared": True})
    assert shared.status_code == 200 and shared.json()["shared"] is True and shared.json()["revision"] > conn["revision"]
    stale = await admin.post(f"{PATH}/connections/{conn['id']}/sharing", json={"revision": conn["revision"], "shared": False})
    assert stale.status_code == 409
    act(bob)
    seen = [c for c in (await admin.get(f"{PATH}/connections")).json() if c["id"] == conn["id"]][0]
    assert seen["shared"] is True and seen["mine"] is False and seen["can_manage"] is True
    tested = await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": seen["revision"]})
    assert tested.status_code == 200, "any admin manages a shared connection"
    # Bob did not add it: sharing is not his call in either direction.
    again = await admin.post(f"{PATH}/connections/{conn['id']}/sharing", json={"revision": tested.json()["revision"], "shared": True})
    assert again.status_code == 403 and code_of(again) == "only_the_creator_can_share"
    pull = await admin.post(f"{PATH}/connections/{conn['id']}/sharing", json={"revision": tested.json()["revision"], "shared": False})
    assert pull.status_code == 403 and code_of(pull) == "only_the_creator_can_unshare"


async def test_a_member_cannot_share_a_connection_that_someone_else_added(admin, act, people, server):
    alice, bob = people
    act(None)
    owner_conn = await make(admin, name="Owner's")
    act(alice)
    assert (await admin.post(f"{PATH}/connections/{owner_conn['id']}/sharing",
                             json={"revision": owner_conn["revision"], "shared": True})).status_code == 404
    act(None)
    shared = await admin.post(f"{PATH}/connections/{owner_conn['id']}/sharing", json={"revision": owner_conn["revision"], "shared": True})
    assert shared.status_code == 200
    act(alice)
    refused = await admin.post(f"{PATH}/connections/{owner_conn['id']}/sharing",
                               json={"revision": shared.json()["revision"], "shared": False})
    assert refused.status_code == 403


async def test_the_workspace_owner_can_switch_off_and_remove_but_not_use_a_private_connection(admin, act, people, server):
    alice, bob = people
    act(alice)
    conn = await ready(admin)
    act(None)
    seen = [c for c in (await admin.get(f"{PATH}/connections")).json() if c["id"] == conn["id"]][0]
    assert seen["can_manage"] is False and seen["mine"] is False
    base = f"{PATH}/connections/{conn['id']}"
    assert (await admin.post(f"{base}/test", json={"revision": seen["revision"]})).status_code == 404
    assert (await admin.post(f"{base}/enable", json={"revision": seen["revision"]})).status_code == 404
    assert (await admin.post(f"{base}/sharing", json={"revision": seen["revision"], "shared": True})).status_code == 403
    assert (await admin.get(f"{base}/tools")).status_code == 404
    off = await admin.post(f"{base}/disable", json={"revision": seen["revision"]})
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert (await admin.delete(base)).status_code == 204
    act(alice)
    assert conn["id"] not in await ids_seen(admin)


async def test_an_owner_can_take_back_a_connection_a_member_shared(admin, act, people, server):
    alice, _ = people
    act(alice)
    conn = await make(admin)
    shared = (await admin.post(f"{PATH}/connections/{conn['id']}/sharing", json={"revision": conn["revision"], "shared": True})).json()
    act(None)
    back = await admin.post(f"{PATH}/connections/{conn['id']}/sharing", json={"revision": shared["revision"], "shared": False})
    assert back.status_code == 200 and back.json()["shared"] is False


async def test_grants_on_a_private_connection_belong_to_its_creator(admin, act, people, server, factory, owner):
    alice, bob = people
    act(alice)
    conn = await ready(admin)
    agent = await agent_id(factory, owner)
    path = f"{PATH}/connections/{conn['id']}/agents/{agent}/grants"
    act(bob)
    assert (await admin.get(path)).status_code == 404
    refused = await admin.put(path, json={"revision": conn["revision"], "tools": []})
    assert refused.status_code == 404
    act(alice)
    assert (await admin.get(path)).status_code == 200


async def test_a_sign_in_started_by_one_person_cannot_be_finished_in_another_persons_browser(admin, act, people, idp, factory, owner):
    alice, bob = people
    act(alice)
    conn = await oauth_connection(admin)
    _, query = await start(admin, conn, idp)
    act(bob)
    refused = await admin.get(f"{CALLBACK}?state={query['state']}&code={idp.code}")
    assert refused.status_code == 400
    async with factory() as db:
        rows = (await db.execute(select(UserCredential).where(UserCredential.user_id == owner))).scalars().all()
        assert not [r for r in rows if r.provider.startswith(oauth.CREDENTIAL_PREFIX)]
    # The pending sign-in is still there for the person who started it.
    act(alice)
    done = await admin.get(f"{CALLBACK}?state={query['state']}&code={idp.code}")
    assert done.status_code == 200
