"""Workspace identity shared by thread creation, migration and the CLI runner."""

import json
import os
import re
from pathlib import Path
from app.core import tenant_paths

TASK_WORKSPACES_DIR_NAME = "task-workspaces"
_THREAD_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def canonical_workspace(root: str = "") -> Path:
    mine = tenant_paths.root()
    if mine is not None:
        # An account never chooses a folder on the host: its workspace is always its own.
        return (mine / "workspace").resolve()
    base = Path(root).expanduser() if root else Path.home() / ".suparagent" / "local"
    # resolve() uses the filesystem's spelling on Windows. Lowercasing the
    # entire string would change the cwd spelling used by existing CLI sessions.
    return (base / "workspace").resolve()


def task_workspace(agent_id: int | None, thread_key: str, root: str = "", *, base: Path | None = None) -> Path:
    """Create and return an isolated per-thread directory under task-workspaces."""
    if not _THREAD_KEY.fullmatch(thread_key):
        raise ValueError("Invalid thread identifier for a task workspace.")
    bucket = str(agent_id) if agent_id is not None else "general"
    ws_root = base.resolve() if base is not None else canonical_workspace(root)
    path = ws_root / TASK_WORKSPACES_DIR_NAME / bucket / thread_key
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path.resolve()


def workspace_locations_prompt(
    agent_id: int | None,
    cwd: Path | str | None,
    agent_workspace_path: str | None = None,
    *,
    root: str = "",
    base: Path | None = None,
) -> str:
    """Build workspace folder context telling the engine which folders exist for this turn."""
    ws_root = base.resolve() if base is not None else canonical_workspace(root)
    bucket = str(agent_id) if agent_id is not None else "general"
    payload: dict[str, str] = {
        "currentWorkingFolder": str(cwd) if cwd else "Default working directory",
        "otherThreadFiles": str((ws_root / TASK_WORKSPACES_DIR_NAME / bucket).resolve()),
    }
    if agent_id is not None:
        payload["sharedAgentFolder"] = str((ws_root / "agents" / str(agent_id)).resolve())
    if agent_workspace_path:
        payload["configuredProjectFolder"] = str(agent_workspace_path)
    return (
        "Workspace folder layout (absolute paths): "
        + json.dumps(payload)
        + ". Different conversations can use separate working folders. Write new task output in "
        "currentWorkingFolder unless an explicit destination was requested, and never modify another thread's folder."
    )


def require_workspace(path: Path) -> Path:
    # Existing conversations must never silently recreate or switch folders.
    if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise OSError("The conversation's workspace is missing or inaccessible. Restore access to its original folder to continue.")
    return path
