from pathlib import Path
from types import SimpleNamespace

from app.services import agent_memory, tool_policy


def _run(workspace, *, agent_id=None):
    return SimpleNamespace(
        agent_id=agent_id,
        ctx=SimpleNamespace(workspace=workspace, media_review_only=False, draft_only=False),
    )


def test_own_file_reads_allow_workspace_and_agent_home_across_modes(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent_dir = tmp_path / "home" / "agents" / "7"
    (agent_dir / "skills" / "demo").mkdir(parents=True)
    monkeypatch.setattr(agent_memory, "agent_home", lambda aid: tmp_path / "home" / "agents" / str(int(aid)))

    run = _run(workspace, agent_id=7)
    ws_file = str(workspace / "notes.md")
    mem_file = str(agent_dir / "MEMORY.md")
    skill_file = str(agent_dir / "skills" / "demo" / "SKILL.md")

    for mode in ("ask", "accept_edits", "approve_for_me", "plan", "full_access"):
        assert tool_policy.automatic("Read", mode, run, {"file_path": ws_file}) == ("allowed", "own_file_read")
        assert tool_policy.automatic("acp:read", mode, run, {"locations": [mem_file]}) == ("allowed", "own_file_read")
        assert tool_policy.automatic("Read", mode, run, {"path": skill_file}) == ("allowed", "own_file_read")
        assert tool_policy.automatic("Grep", mode, run, {"path": "subdir/a.py"}) == ("allowed", "own_file_read")


def test_own_file_reads_still_require_approval_for_external_or_traversal_paths(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    monkeypatch.setattr(agent_memory, "agent_home", lambda aid: tmp_path / "home" / "agents" / str(int(aid)))

    run = _run(workspace, agent_id=7)
    other_agent_file = str(tmp_path / "home" / "agents" / "99" / "MEMORY.md")

    assert tool_policy.automatic("Read", "ask", run, {"file_path": str(outside)}) is None
    assert tool_policy.automatic("Read", "accept_edits", run, {"file_path": "../secret.txt"}) is None
    assert tool_policy.automatic("acp:read", "ask", run, {"locations": [other_agent_file]}) is None
    assert tool_policy.automatic("acp:read", "ask", run, {}) is None
    assert tool_policy.automatic("Read", "ask", _run(None), {"file_path": str(workspace / "a.txt")}) is None


def test_web_search_auto_approves_in_permissive_modes_only(tmp_path):
    run = _run(tmp_path)
    for tool in ("WebSearch", "web_search", "google_web_search", "search_web", "acp:search"):
        for mode in ("accept_edits", "approve_for_me", "full_access"):
            assert tool_policy.automatic(tool, mode, run, {"query": "weather today"}) == ("allowed", "auto_web_search")
        for strict in ("ask", "plan"):
            assert tool_policy.automatic(tool, strict, run, {"query": "weather today"}) is None
