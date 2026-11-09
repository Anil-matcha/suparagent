import pytest
from fastapi import FastAPI
import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import AgentRevision
from app.services import agent_history as hist
from app.services import agent_store as store


@pytest.fixture
async def sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def make(sessions, owner="o1", name="Ada"):
    async with sessions() as db:
        return (await store.create_agent(db, owner_id=owner, name=name, instructions="v1")).id


async def test_first_edit_stores_baseline_then_each_change(sessions):
    aid = await make(sessions)
    async with sessions() as db:
        await store.update_agent(db, aid, instructions="v2")
    async with sessions() as db:
        await store.update_agent(db, aid, instructions="v2")  # no change: no revision
        await store.update_agent(db, aid, title="Lead", instructions="v3")
    async with sessions() as db:
        rows = await hist.list_revisions(db, aid)
    assert [r.number for r in rows] == [3, 2, 1]
    assert [r.source for r in rows] == ["edit", "edit", "baseline"]
    assert rows[0].changed_fields == ["title", "instructions"] and rows[0].actor == "o1"
    assert rows[2].snapshot["instructions"] == "v1" and rows[1].snapshot["instructions"] == "v2"


async def test_secrets_are_redacted_and_not_restored(sessions):
    aid = await make(sessions)
    async with sessions() as db:
        await store.update_agent(db, aid, instructions="use key sk-abcdefghijklmnopqrstuvwxyz123456 always")
    async with sessions() as db:
        await store.update_agent(db, aid, instructions="clean", title="T")
    async with sessions() as db:
        rev = await hist.get_revision(db, aid, 2)
        agent = await store.get_agent(db, aid)
        assert "sk-abcdefghij" not in rev.snapshot["instructions"] and rev.redacted == ["instructions"]
        fields = hist.restorable(rev, agent)
    assert "instructions" not in fields  # the redacted text is never put back


async def test_only_the_newest_twenty_are_kept(sessions):
    aid = await make(sessions)
    for i in range(25):
        async with sessions() as db:
            await store.update_agent(db, aid, instructions=f"edit {i}")
    async with sessions() as db:
        rows = await hist.list_revisions(db, aid)
    assert len(rows) == hist.KEEP and rows[0].number == 26 and rows[-1].number == 7


class FakeRuntime:
    def __init__(self, sessions, owner="o1"):
        self._s, self.owner_id = sessions, owner
        self.engines = None
        self.computers = None

    def session(self):
        return self._s()


@pytest.fixture
def client(sessions, monkeypatch):
    from app.routers import agents as router
    from app.services.local_access_policy import require_local_access
    app = FastAPI()
    app.include_router(router.router)
    rt = FakeRuntime(sessions)
    app.dependency_overrides[router.get_runtime] = lambda: rt
    app.dependency_overrides[require_local_access] = lambda: None

    async def no_computers(_rt, agents):
        return agents
    monkeypatch.setattr(router, "_with_computers", no_computers)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_list_and_restore_over_http(sessions, client):
    aid = await make(sessions)
    async with sessions() as db:
        await store.update_agent(db, aid, instructions="v2", approval_mode="plan")
    async with sessions() as db:
        await store.update_agent(db, aid, instructions="v3")
    async with client as c:
        listed = (await c.get(f"/agents/{aid}/revisions")).json()
        assert [r["number"] for r in listed] == [3, 2, 1] and "snapshot" not in listed[0]
        assert (await c.get(f"/agents/{aid}/revisions/1")).json()["snapshot"]["instructions"] == "v1"
        stale = await c.post(f"/agents/{aid}/revisions/1/restore", json={"expected_revision": 2})
        assert stale.status_code == 409 and stale.json()["detail"]["error_code"] == "revision_conflict"
        ok = await c.post(f"/agents/{aid}/revisions/1/restore", json={"expected_revision": 3})
        assert ok.status_code == 200, ok.text
        assert ok.json()["instructions"] == "v1" and ok.json()["approval_mode"] == "ask"
        after = (await c.get(f"/agents/{aid}/revisions")).json()
        assert after[0]["number"] == 4 and after[0]["source"] == "restore" and after[0]["restored_from"] == 1
        assert (await c.get(f"/agents/{aid}/revisions/99")).status_code == 404


async def test_other_owner_gets_404(sessions, client):
    other = await make(sessions, owner="o2", name="Bob")
    async with client as c:
        assert (await c.get(f"/agents/{other}/revisions")).status_code == 404
        assert (await c.post(f"/agents/{other}/revisions/1/restore")).status_code == 404
