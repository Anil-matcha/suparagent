from pathlib import Path
from types import SimpleNamespace

from app.services import tool_policy


def _run(workspace):
    return SimpleNamespace(ctx=SimpleNamespace(workspace=workspace, media_review_only=False, draft_only=False))


def test_full_access_allows_a_plain_command(tmp_path):
    assert tool_policy.automatic("Bash", "full_access", _run(tmp_path), {"command": "git status"}) == ("allowed", "full_access")


def test_other_modes_do_not_allow_a_command(tmp_path):
    for mode in ("ask", "accept_edits", "plan"):
        assert tool_policy.automatic("Bash", mode, _run(tmp_path), {"command": "git status"}) is None


def test_full_access_still_holds_a_never_list_command(tmp_path):
    assert tool_policy.automatic("Bash", "full_access", _run(tmp_path), {"command": "rm -rf /"}) is None


def test_full_access_holds_an_unreadable_or_missing_command(tmp_path):
    assert tool_policy.automatic("Bash", "full_access", _run(tmp_path), {"command": "echo $(("}) in (None, ("allowed", "full_access"))
    assert tool_policy.automatic("Bash", "full_access", _run(tmp_path), {}) is None
    assert tool_policy.automatic("Bash", "full_access", _run(tmp_path), None) is None


def test_full_access_allows_edits_inside_the_workspace_only(tmp_path):
    inside, outside = tmp_path / "a.txt", Path(tmp_path).parent / "elsewhere.txt"
    assert tool_policy.automatic("Write", "full_access", _run(tmp_path), {"file_path": str(inside)}) == ("allowed", "full_access")
    assert tool_policy.automatic("Edit", "full_access", _run(tmp_path), {"file_path": str(outside)}) is None
    assert tool_policy.automatic("Write", "full_access", _run(tmp_path), {"file_path": str(tmp_path / ".." / "x")}) is None
    assert tool_policy.automatic("Write", "full_access", _run(None), {"file_path": str(inside)}) is None


def test_full_access_never_covers_connected_apps_or_marketplace_tools(tmp_path):
    run = _run(tmp_path)
    run.ctx.community_tools = []
    assert tool_policy.automatic("mcp__community__anything", "full_access", run, {}) == ("denied", "marketplace_tool_not_granted")


def test_full_access_allows_a_codex_patch_inside_the_workspace_only(tmp_path):
    inside = {"changes": [{"path": str(tmp_path / "a.md")}]}
    outside = {"changes": [{"path": str(tmp_path / "a.md")}, {"path": str(tmp_path.parent / "b.md")}]}
    assert tool_policy.automatic("apply_patch", "full_access", _run(tmp_path), inside) == ("allowed", "full_access")
    assert tool_policy.automatic("apply_patch", "full_access", _run(tmp_path), outside) is None
    assert tool_policy.automatic("apply_patch", "full_access", _run(tmp_path), {**inside, "grant_root": "/"}) is None
    assert tool_policy.automatic("apply_patch", "full_access", _run(tmp_path), {"changes": []}) is None


def test_codex_full_access_lifts_its_sandbox_but_keeps_asking_this_app():
    from app.services.codex_adapter import codex_policy
    policy = codex_policy("full_access", tools=True)
    assert (policy.approval, policy.sandbox, policy.sandbox_type) == ("untrusted", "danger-full-access", "dangerFullAccess")
    assert codex_policy("full_access", tools=False).sandbox == "read-only"
