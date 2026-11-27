import json
import os
import time
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app
from app.services import environment_identity, local_claude_runner, task_workspace_retention, workspaces
from tests.services.thread_harness import *  # noqa: F401,F403


def test_environment_identity_stable_across_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SQLITE_PATH", str(tmp_path / "suparagent.db"))
    monkeypatch.setattr(settings, "DATABASE_BACKEND", "sqlite")
    first = environment_identity.descriptor()
    second = environment_identity.descriptor()
    assert first["service"] == "suparagent"
    assert first["environment_id"] == second["environment_id"]
    assert (tmp_path / "environment-id").read_text(encoding="utf-8").strip() == first["environment_id"]


async def test_well_known_environment_public_route(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SQLITE_PATH", str(tmp_path / "suparagent.db"))
    monkeypatch.setattr(settings, "DATABASE_BACKEND", "sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        res = await client.get("/.well-known/suparagent/environment")
        api_res = await client.get("/api/v1/environment")
    assert res.status_code == 200
    assert api_res.status_code == 200
    body = res.json()
    assert body["service"] == "suparagent"
    assert body["environment_id"] == api_res.json()["environment_id"]
    assert body["capabilities"]["task_workspaces"] is True


async def test_isolated_task_workspaces_and_retention(rt, sessions, owner, monkeypatch, tmp_path):
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    t1 = await rt.create_thread(isolate_workspace=True)
    t2 = await rt.create_thread(isolate_workspace=True)
    p1 = Path(t1.workspace_path)
    p2 = Path(t2.workspace_path)
    assert p1 != p2
    assert p1.is_dir() and p2.is_dir()
    assert p1.parent == p2.parent == (tmp_path / "task-workspaces" / "general").resolve()

    prompt = workspaces.workspace_locations_prompt(None, p1, base=tmp_path)
    assert json.dumps(str(p1)) in prompt
    assert "otherThreadFiles" in prompt

    old_ts = time.time() - 40 * 86400
    root_parent = tmp_path / "canonical"
    ws_isolated = workspaces.task_workspace(5, "thread_old", root=str(root_parent))
    ws_active = workspaces.task_workspace(5, "thread_keep", root=str(root_parent))
    os.utime(ws_isolated, (old_ts, old_ts))
    os.utime(ws_active, (old_ts, old_ts))
    deleted = task_workspace_retention.cleanup_expired_task_workspaces(
        {str(ws_active)},
        max_age_seconds=30 * 86400,
        root=str(root_parent),
        now=time.time(),
    )
    assert str(ws_isolated) in deleted
    assert not ws_isolated.exists()
    assert ws_active.exists()
