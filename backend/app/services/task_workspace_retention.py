"""Retention cleanup for expired isolated per-thread task workspace folders."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from app.services.workspaces import TASK_WORKSPACES_DIR_NAME, canonical_workspace

DEFAULT_RETENTION_SECONDS = 30 * 86400


def cleanup_expired_task_workspaces(
    active_paths: set[str],
    *,
    max_age_seconds: int = DEFAULT_RETENTION_SECONDS,
    root: str = "",
    now: float | None = None,
) -> list[str]:
    """Remove isolated task-workspace directories not in `active_paths` whose mtime
    exceeds `max_age_seconds`. Never follows symlinks or deletes outside task-workspaces."""
    base = (canonical_workspace(root) / TASK_WORKSPACES_DIR_NAME).resolve()
    if not base.is_dir() or base.is_symlink():
        return []

    cutoff = (now if now is not None else time.time()) - max(0, max_age_seconds)
    active_resolved = {str(Path(p).resolve()) for p in active_paths if p}
    removed: list[str] = []

    for bucket in sorted(base.iterdir()):
        if bucket.is_symlink() or not bucket.is_dir():
            continue
        for folder in sorted(bucket.iterdir()):
            if folder.is_symlink() or not folder.is_dir():
                continue
            resolved = str(folder.resolve())
            if resolved in active_resolved:
                continue
            try:
                mtime = folder.stat().st_mtime
            except OSError:
                continue
            if mtime <= cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                removed.append(resolved)
        try:
            if not any(bucket.iterdir()):
                bucket.rmdir()
        except OSError:
            pass
    return removed
