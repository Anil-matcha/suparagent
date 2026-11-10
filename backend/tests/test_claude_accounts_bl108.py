import os
import stat
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.routers import claude_accounts as route
from app.services import agent_store as store
from app.services import claude_accounts as acc
from app.services import local_claude_runner as runner
from app.services.local_access_policy import require_local_access


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "base_dir", lambda: tmp_path / "claude-accounts")
    return tmp_path / "claude-accounts"


def test_create_list_rename_remove_and_isolated_folders(data_dir):
    a = acc.create("  Work  ")
    b = acc.create("Personal")
    assert a["label"] == "Work" and a["id"] != b["id"]
    assert [x["label"] for x in acc.list_accounts()] == ["Work", "Personal"]
    assert all(set(x) == {"id", "label", "created_at"} for x in acc.list_accounts())
    fa, fb = acc.config_dir(a["id"]), acc.config_dir(b["id"])
    assert fa != fb and fa.is_absolute() and fa.parent == data_dir.resolve()
    assert stat.S_IMODE(os.stat(fa).st_mode) == 0o700 and stat.S_IMODE(os.stat(data_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(data_dir / "accounts.json").st_mode) == 0o600
    (fa / "token").write_text("secret")
    assert acc.rename(a["id"], "Work 2")["label"] == "Work 2"
    acc.remove(a["id"])
    assert not fa.exists() and fb.exists() and [x["id"] for x in acc.list_accounts()] == [b["id"]]


def test_labels_unique_validated_and_capped():
    acc.create("One")
    for bad in ("one", " ", "x" * 61, "a\x00b", None, 5):
        with pytest.raises(acc.AccountError):
            acc.create(bad)
    for i in range(acc.MAX_ACCOUNTS - 1):
        acc.create(f"n{i}")
    with pytest.raises(acc.AccountError) as e:
        acc.create("overflow")
    assert e.value.code == "too_many_accounts"


def test_default_account_is_none_and_missing_account_never_falls_back():
    assert acc.config_dir(None) is None
    made = acc.create("Gone")
    acc.remove(made["id"])
    for bad in (made["id"], "../../etc", "acct-zzzz", ""):
        with pytest.raises(acc.AccountError):
            acc.config_dir(bad)


def test_account_env_replaces_folder_and_drops_shared_credentials(tmp_path):
    env = {"CLAUDE_CONFIG_DIR": "/home/u/.claude", "ANTHROPIC_API_KEY": "k", "CLAUDE_CODE_OAUTH_TOKEN": "t", "PATH": "/bin"}
    out = runner.account_env(env, tmp_path)
    assert out == {"CLAUDE_CONFIG_DIR": str(tmp_path), "PATH": "/bin"}
    assert "ANTHROPIC_API_KEY" in env  # input untouched
    with pytest.raises(ValueError):
        runner.account_env(env, Path("relative"))


def test_run_prompt_passes_the_account_folder(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(runner, "resolve_claude_executable", lambda: Path("/bin/claude"))
    monkeypatch.setattr(runner, "build_args", lambda *a, **k: ["claude"])
    monkeypatch.setattr(runner, "run_blocking", lambda *a, **k: seen.update(k) or "ok")
    runner._run_blocking("hi", "sid", None, None, config_dir=tmp_path)
    assert seen["config_dir"] == tmp_path
    seen.clear()
    runner._run_blocking("hi", "sid", None, None)
    assert seen["config_dir"] is None  # default account: behaviour unchanged


async def test_agent_stores_the_account_and_defaults_to_none(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        agent = await store.create_agent(db, owner_id="o", name="Ada")
        assert agent.claude_account is None
        made = acc.create("Work")
        agent = await store.update_agent(db, agent.id, claude_account=made["id"])
        assert agent.claude_account == made["id"]
        assert (await store.duplicate_agent(db, agent.id)).claude_account == made["id"]
    await engine.dispose()


@pytest.fixture
async def client(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class RT:
        _runs = {}
        _starting_turns = {}
        _closing = False
        adapters = {"claude": object()}

        def session(self):
            return sessions()

        class claude_login:
            started = []

            @classmethod
            async def start(cls, owner, executable, folder=None):
                cls.started.append((owner, folder))
                return {"id": "flow1", "phase": "running"}

    rt = RT()
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[require_local_access] = lambda: None
    app.dependency_overrides[route.get_runtime] = lambda: rt
    admin = {"on": True}

    def owner(request):
        if not admin["on"]:
            raise route._error(403, "admin_required", "no")
        return "owner:1"

    monkeypatch.setattr(route, "_login_owner", owner)
    monkeypatch.setattr("app.services.local_claude_runner.resolve_claude_executable", lambda: Path("/bin/claude"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        c.sessions, c.rt, c.admin = sessions, rt, admin
        yield c
    await engine.dispose()


async def test_routes_manage_accounts_and_start_login_for_one(client):
    r = await client.post("/engines/claude/accounts", json={"label": "Work"})
    assert r.status_code == 201
    aid = r.json()["id"]
    assert (await client.post("/engines/claude/accounts", json={"label": "work"})).status_code == 409
    assert (await client.patch(f"/engines/claude/accounts/{aid}", json={"label": "Job"})).json()["label"] == "Job"
    assert [a["label"] for a in (await client.get("/engines/claude/accounts")).json()] == ["Job"]
    started = await client.post(f"/engines/claude/accounts/{aid}/login")
    assert started.status_code == 202 and started.json()["id"] == "flow1"
    owner, folder = client.rt.claude_login.started[-1]
    assert folder == acc.config_dir(aid)
    assert (await client.post("/engines/claude/accounts/acct-000000000000/login")).status_code == 404
    assert (await client.delete(f"/engines/claude/accounts/{aid}")).status_code == 204
    assert (await client.get("/engines/claude/accounts")).json() == []


async def test_routes_are_owner_only(client):
    client.admin["on"] = False
    for call in (client.get("/engines/claude/accounts"), client.post("/engines/claude/accounts", json={"label": "x"})):
        assert (await call).status_code == 403


async def test_cannot_remove_an_account_an_agent_uses(client):
    aid = (await client.post("/engines/claude/accounts", json={"label": "Work"})).json()["id"]
    async with client.sessions() as db:
        await store.create_agent(db, owner_id="o", name="Ada", claude_account=aid)
    r = await client.delete(f"/engines/claude/accounts/{aid}")
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "account_in_use"
    assert acc.exists(aid)
