"""Stable server identity and capability descriptor for connected clients."""

from __future__ import annotations

import os
import re
import socket
import sys
from pathlib import Path
from uuid import uuid4

from app.core import tenant_paths
from app.core.config import settings
from app.desktop import profile

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def environment_file() -> Path:
    """Path where this server's persistent environment UUID is stored."""
    if profile.active():
        return profile.current().data_dir / "environment-id"
    mine = tenant_paths.root()
    if mine is not None:
        return mine / "environment-id"
    if settings.DATABASE_KIND == "sqlite":
        return settings.SQLITE_FILE.parent / "environment-id"
    return Path.home() / ".suparagent" / "local" / "environment-id"


def _read_version() -> str:
    try:
        from app.core.version import app_version
        return app_version()
    except Exception:  # noqa: BLE001
        return "0.0.0"


def environment_id() -> str:
    """Return the server's stable UUIDv4, creating it atomically if absent."""
    path = environment_file()
    try:
        existing = path.read_text(encoding="utf-8").strip().lower()
        if _UUID_RE.fullmatch(existing):
            return existing
    except OSError:
        pass

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    generated = str(uuid4())
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
        try:
            os.write(fd, (generated + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        return generated
    except FileExistsError:
        existing = path.read_text(encoding="utf-8").strip().lower()
        if _UUID_RE.fullmatch(existing):
            return existing
        path.write_text(generated + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return generated


def descriptor() -> dict:
    """Public metadata served at /.well-known/suparagent/environment."""
    hostname = socket.gethostname() or "Suparagent Server"
    return {
        "environment_id": environment_id(),
        "service": "suparagent",
        "label": hostname,
        "platform": sys.platform,
        "version": _read_version(),
        "capabilities": {
            "pairing": True,
            "backup_api": True,
            "task_workspaces": True,
        },
    }
