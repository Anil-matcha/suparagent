"""Saved apps over HTTP: create, versions, pin, rename, owner isolation, rooms, chat deletion, follow-ups, cap."""
import re

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select

from app.db.models import SavedApp
from app.routers.saved_apps import router as saved_apps_router
from app.routers.threads import get_runtime
from app.services import saved_apps as svc
from app.services import thread_store as store
from tests.routers.test_agents_api import client_for, local_enabled  # noqa: F401
from tests.services.thread_harness import cleanup, cli, new_thread, owner, rt, sessions  # noqa: F401

ID = re.compile(r"^sa_[A-Za-z0-9_-]{16}$")


def app_for(rt) -> FastAPI:
    app = FastAPI()
    app.include_router(saved_apps_router, prefix="/api/v1")
    app.dependency_overrides[get_runtime] = lambda: rt
    return app


@pytest.fixture
async def api(rt, cli):
    async with client_for(app_for(rt)) as client:
        yield client


@pytest.fixture(autouse=True)
async def apps_cleanup(sessions, owner):
    yield
    async with sessions() as db:
        await db.execute(delete(SavedApp).where(SavedApp.owner_id.like("od_rt_%")))
        await db.commit()


async def show(sessions, thread_id, page_id="pg1", rev=1, title="Tip calc", html=None):
    async with sessions() as db:
        await store.append_event(db, thread_id, "page.shown", {
            "page_id": page_id, "rev": rev, "title": title, "html": html or f"<p>rev {rev}</p>",
            "height": 360, "frameless": False, "uses": ["state"],
        })


async def save(api, thread_id, page_id="pg1"):
    return await api.post("/api/v1/saved-apps", json={"thread_id": thread_id, "page_id": page_id})


async def test_create_is_idempotent_and_snapshots_every_revision(api, sessions, owner):
    thread = await new_thread(sessions, owner)
    for rev in (1, 2, 3):
        await show(sessions, thread.id, rev=rev)
    first = await save(api, thread.id)
    assert first.status_code == 201, first.text
    assert first.headers["cache-control"] == "no-store"
    item = first.json()["app"]
    assert ID.match(item["id"])
    assert item["rev"] == 3 and [v["rev"] for v in item["versions"]] == [1, 2, 3]
    assert item["name"] == "Tip calc" and item["chat"] is True and item["pinned_rev"] is None
    assert item["thread_id"] == thread.id and item["agent_name"] is None and item["uses"] == ["state"]
    second = await save(api, thread.id)
    assert second.status_code == 200 and second.json()["app"]["id"] == item["id"]
    listed = (await api.get("/api/v1/saved-apps")).json()["apps"]
    assert [a["id"] for a in listed] == [item["id"]]
    assert (await api.get(f"/api/v1/saved-apps/{item['id']}")).json()["app"]["id"] == item["id"]


async def test_unknown_thread_or_page_is_404(api, sessions, owner):
    thread = await new_thread(sessions, owner)
    assert (await save(api, 999999)).status_code == 404
    assert (await save(api, thread.id, "nope")).status_code == 404
    assert (await api.post("/api/v1/saved-apps", json={"thread_id": thread.id, "page_id": "bad id!"})).status_code == 422


async def test_versions_keep_the_newest_20_and_the_pinned_one(api, sessions, owner):
    thread = await new_thread(sessions, owner)
    for rev in range(1, 26):
        await show(sessions, thread.id, rev=rev)
    item = (await save(api, thread.id)).json()["app"]
    assert [v["rev"] for v in item["versions"]] == list(range(6, 26))
    assert (await api.patch(f"/api/v1/saved-apps/{item['id']}", json={"pinned_rev": 3})).status_code == 422
    pinned = await api.patch(f"/api/v1/saved-apps/{item['id']}", json={"pinned_rev": 6})
    assert pinned.status_code == 200 and pinned.json()["app"]["pinned_rev"] == 6
    for rev in (26, 27):
        await show(sessions, thread.id, rev=rev)
        async with sessions() as db:
            await svc.append_version(db, owner, "pg1", {"page_id": "pg1", "rev": rev, "title": "t", "html": "<p>x</p>"})
    after = (await api.get(f"/api/v1/saved-apps/{item['id']}")).json()["app"]
    revs = [v["rev"] for v in after["versions"]]
    assert 6 in revs and revs[-1] == 27 and len(revs) == 21 and 7 not in revs and 8 in revs
    assert (await api.get(f"/api/v1/saved-apps/{item['id']}/document")).json()["rev"] == 6  # pinned wins
    unpinned = await api.patch(f"/api/v1/saved-apps/{item['id']}", json={"pinned_rev": None})
    assert unpinned.json()["app"]["pinned_rev"] is None
    assert (await api.get(f"/api/v1/saved-apps/{item['id']}/document")).json()["rev"] == 27


async def test_rename_is_trimmed_and_validated(api, sessions, owner):
    thread = await new_thread(sessions, owner)
    await show(sessions, thread.id)
    item = (await save(api, thread.id)).json()["app"]
    url = f"/api/v1/saved-apps/{item['id']}"
    renamed = await api.patch(url, json={"name": "  Budget  "})
    assert renamed.json()["app"]["name"] == "Budget"
    for bad in ("", "   ", "x" * 81):
        assert (await api.patch(url, json={"name": bad})).status_code == 422
    assert (await api.patch(url, json={"name": None})).status_code == 422
    assert (await api.patch(url, json={"pinned_rev": 0})).status_code == 422
    assert (await api.patch(url, json={"nope": 1})).status_code == 422
    assert (await api.get(url)).json()["app"]["name"] == "Budget"


async def test_document_rev_selection_and_validation(api, sessions, owner):
    thread = await new_thread(sessions, owner)
    for rev in (1, 2):
        await show(sessions, thread.id, rev=rev)
    item = (await save(api, thread.id)).json()["app"]
    url = f"/api/v1/saved-apps/{item['id']}/document"
    newest = await api.get(url)
    assert newest.headers["cache-control"] == "no-store"
    body = newest.json()
    assert set(body) == {"id", "name", "rev", "title", "html", "height", "frameless", "uses", "versions", "pinned_rev", "chat"}
    assert body["rev"] == 2 and body["html"] == "<p>rev 2</p>"
    assert (await api.get(url, params={"rev": 1})).json()["html"] == "<p>rev 1</p>"
    assert (await api.get(url, params={"rev": 9})).status_code == 404
    for bad in ("abc", "1234567", "-1", ""):
        assert (await api.get(url, params={"rev": bad})).status_code == 422, bad


async def test_another_owner_gets_404(api, rt, sessions, owner):
    thread = await new_thread(sessions, owner)
    await show(sessions, thread.id)
    item = (await save(api, thread.id)).json()["app"]
    rt.owner_id = "od_rt_someone_else"
    assert (await api.get("/api/v1/saved-apps")).json() == {"apps": []}
    for call in (api.get(f"/api/v1/saved-apps/{item['id']}"), api.get(f"/api/v1/saved-apps/{item['id']}/document"),
                 api.patch(f"/api/v1/saved-apps/{item['id']}", json={"name": "x"}), api.delete(f"/api/v1/saved-apps/{item['id']}")):
        assert (await call).status_code == 404
    assert (await save(api, thread.id)).status_code == 404
    rt.owner_id = owner
    assert (await api.get(f"/api/v1/saved-apps/{item['id']}")).status_code == 200


async def test_a_room_thread_is_refused(api, sessions, owner):
    thread = await new_thread(sessions, owner)
    await show(sessions, thread.id)
    from app.db.models import Thread
    async with sessions() as db:
        (await db.get(Thread, thread.id)).room_id = 12345
        try:
            await db.commit()
        except Exception:
            pytest.skip("room rows need a room")
    response = await save(api, thread.id)
    assert response.status_code == 409 and response.json()["detail"]["error_code"] == "room_thread"


async def test_deleting_the_chat_leaves_the_app_readable(api, rt, sessions, owner):
    thread = await new_thread(sessions, owner)
    await show(sessions, thread.id)
    item = (await save(api, thread.id)).json()["app"]
    async with sessions() as db:
        await store.delete_thread(db, thread.id)
    after = (await api.get(f"/api/v1/saved-apps/{item['id']}")).json()["app"]
    assert after["chat"] is False and after["rev"] == 1
    document = (await api.get(f"/api/v1/saved-apps/{item['id']}/document")).json()
    assert document["chat"] is False and document["html"] == "<p>rev 1</p>"


async def test_a_follow_up_revision_is_appended_once(api, sessions, owner):
    thread = await new_thread(sessions, owner)
    await show(sessions, thread.id)
    item = (await save(api, thread.id)).json()["app"]
    data = {"page_id": "pg1", "rev": 2, "title": "Tip calc v2", "html": "<p>two</p>", "height": 400, "frameless": True, "uses": []}
    async with sessions() as db:
        assert await svc.append_version(db, owner, "pg1", data) is True
    async with sessions() as db:
        assert await svc.append_version(db, owner, "pg1", data) is False  # same rev: nothing added
    async with sessions() as db:
        assert await svc.append_version(db, owner, "other-page", data) is False  # no app for it
    after = (await api.get(f"/api/v1/saved-apps/{item['id']}")).json()["app"]
    assert [v["rev"] for v in after["versions"]] == [1, 2] and after["uses"] == []
    document = (await api.get(f"/api/v1/saved-apps/{item['id']}/document")).json()
    assert document["rev"] == 2 and document["frameless"] is True and document["height"] == 400


async def test_the_store_holds_at_most_200_apps(api, sessions, owner, monkeypatch):
    thread = await new_thread(sessions, owner)
    await show(sessions, thread.id, page_id="first")
    monkeypatch.setattr(svc, "APPS_MAX", 2)
    await show(sessions, thread.id, page_id="second")
    await show(sessions, thread.id, page_id="third")
    assert (await save(api, thread.id, "first")).status_code == 201
    assert (await save(api, thread.id, "second")).status_code == 201
    full = await save(api, thread.id, "third")
    assert full.status_code == 507 and full.json()["detail"]["error_code"] == "store_full"
    assert (await save(api, thread.id, "first")).status_code == 200  # an existing app is still reachable


async def test_delete_removes_the_app(api, sessions, owner):
    thread = await new_thread(sessions, owner)
    await show(sessions, thread.id)
    item = (await save(api, thread.id)).json()["app"]
    assert (await api.delete(f"/api/v1/saved-apps/{item['id']}")).json() == {"ok": True}
    assert (await api.get(f"/api/v1/saved-apps/{item['id']}")).status_code == 404
    assert (await api.get("/api/v1/saved-apps/not-an-id")).status_code == 404


async def test_update_page_through_the_bridge_appends_to_the_saved_app(api, rt, sessions, owner):
    from types import SimpleNamespace
    from app.services import pages_bridge

    thread = await new_thread(sessions, owner)
    await show(sessions, thread.id)
    item = (await save(api, thread.id)).json()["app"]

    class Recorder:
        async def emit(self, type_, data):
            async with sessions() as db:
                await store.append_event(db, thread.id, type_, data)
            return True

    run = SimpleNamespace(turn_id=1, thread_id=thread.id, ctx=SimpleNamespace(aborted=False), recorder=Recorder())
    bridge = pages_bridge.PagesBridge(rt, run)
    try:
        result = await bridge.execute("update_page", {"page_id": "pg1", "html": "<p>from the tool</p>"})
    finally:
        await bridge.close()
    assert "rev 2" in result["text"]
    document = (await api.get(f"/api/v1/saved-apps/{item['id']}/document")).json()
    assert document["rev"] == 2 and document["html"] == "<p>from the tool</p>"
