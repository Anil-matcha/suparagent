"""Undo a task's file changes (BL-102): snapshot a workspace before a turn, rewind it later.

A snapshot is a manifest (path -> content hash) plus content-addressed copies of the files,
kept in a folder outside the workspace so an agent cannot reach or edit it. Unchanged files
are shared between snapshots. Safety rules:

* Caps: at most MAX_FILES files, MAX_FILE_BYTES per file and MAX_TOTAL_BYTES in all. A
  workspace over a cap gets no snapshot (never a partial one), so a restore can never
  delete files it did not know about.
* Skipped: dependency and cache folders, `.git`, and symbolic links (never followed).
* Restore only writes or deletes inside the workspace (every path is re-checked after
  resolving), and first takes a "before undo" snapshot, so an undo can itself be undone.
* Only the newest KEEP snapshots of a workspace are kept; unreferenced copies are deleted.
"""

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from app.core import tenant_paths

MAX_FILES = 5000
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
KEEP = 10
IGNORED_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    ".next", ".cache", ".tox", ".gradle", "target",
})


class SnapshotError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def store_dir_for(workspace: str | Path) -> Path:
    """Where one workspace's snapshots live: beside the workspace data, never inside it."""
    mine = tenant_paths.root()
    base = (mine / "snapshots") if mine is not None else (Path.home() / ".suparagent" / "local" / "snapshots")
    key = hashlib.sha256(str(Path(workspace).resolve()).encode()).hexdigest()[:24]
    return base / key


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _scan(root: Path):
    """(rel, abs, stat) for each regular file; raises SnapshotError over a cap."""
    out = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            if st.st_size > MAX_FILE_BYTES:
                continue  # left alone by snapshot and restore alike
            total += st.st_size
            if len(out) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                raise SnapshotError("workspace_too_large", "This workspace is too large to snapshot for undo.", 413)
            out.append((Path(full).relative_to(root).as_posix(), full, st))
    return out


def _blobs(store: Path) -> Path:
    return store / "blobs"


def _manifest_path(store: Path, snap_id: str) -> Path:
    return store / f"{snap_id}.json"


def _check_id(snap_id: str) -> str:
    if not snap_id or not all(c.isalnum() or c in "-_" for c in snap_id) or len(snap_id) > 64:
        raise SnapshotError("bad_snapshot_id", "Not a valid snapshot id.")
    return snap_id


def _load(store: Path, snap_id: str) -> dict:
    try:
        return json.loads(_manifest_path(store, _check_id(snap_id)).read_text())
    except (OSError, ValueError):
        raise SnapshotError("snapshot_not_found", "That snapshot does not exist.", 404) from None


def list_snapshots(store: Path) -> list[dict]:
    items = []
    for path in store.glob("*.json") if store.is_dir() else []:
        try:
            data = json.loads(path.read_text())
            items.append({k: data[k] for k in ("id", "label", "created", "files")} | {"files": len(data["files"])})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(items, key=lambda i: (i["created"], i["id"]), reverse=True)


def snapshot(workspace: str | Path, store: Path, snap_id: str, label: str = "") -> dict | None:
    """Record the workspace under `snap_id`. Returns the summary, or None for a missing workspace."""
    root = Path(workspace)
    if not root.is_dir():
        return None
    _check_id(snap_id)
    files = _scan(root)
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    blobs = _blobs(store)
    blobs.mkdir(exist_ok=True, mode=0o700)
    # Reuse hashes of files whose size and mtime are unchanged since the newest snapshot.
    known: dict[str, dict] = {}
    newest = list_snapshots(store)
    if newest:
        try:
            known = _load(store, newest[0]["id"])["files"]
        except SnapshotError:
            known = {}
    manifest: dict[str, dict] = {}
    for rel, full, st in files:
        prior = known.get(rel)
        if prior and prior["size"] == st.st_size and prior["mtime"] == st.st_mtime_ns and (blobs / prior["sha"]).exists():
            sha = prior["sha"]
        else:
            data = Path(full).read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            blob = blobs / sha
            if not blob.exists():
                tmp = blobs / f".{sha}.tmp"
                tmp.write_bytes(data)
                os.replace(tmp, blob)
        manifest[rel] = {"sha": sha, "size": st.st_size, "mtime": st.st_mtime_ns, "mode": st.st_mode & 0o777}
    record = {"id": snap_id, "label": label[:120], "created": time.time(), "files": manifest}
    tmp = store / f".{snap_id}.tmp"
    tmp.write_text(json.dumps(record))
    os.replace(tmp, _manifest_path(store, snap_id))
    prune(store)
    return {"id": snap_id, "label": record["label"], "created": record["created"], "files": len(manifest)}


def prune(store: Path, keep: int = KEEP) -> None:
    items = list_snapshots(store)
    for old in items[keep:]:
        _manifest_path(store, old["id"]).unlink(missing_ok=True)
    live: set[str] = set()
    for item in items[:keep]:
        try:
            live.update(f["sha"] for f in _load(store, item["id"])["files"].values())
        except SnapshotError:
            pass
    for blob in _blobs(store).glob("*") if _blobs(store).is_dir() else []:
        if blob.name not in live and not blob.name.startswith("."):
            blob.unlink(missing_ok=True)


def restore(workspace: str | Path, store: Path, snap_id: str) -> dict:
    """Rewind the workspace to `snap_id`. Returns counts of files restored, removed and unchanged."""
    root = Path(workspace)
    if not root.is_dir():
        raise SnapshotError("workspace_missing", "The workspace folder is missing.", 409)
    record = _load(store, snap_id)
    wanted: dict[str, dict] = record["files"]
    blobs = _blobs(store)
    for entry in wanted.values():
        if not (blobs / entry["sha"]).is_file():
            raise SnapshotError("snapshot_damaged", "That snapshot's files are no longer available.", 409)
    # Make undo undoable. Cap errors are not a reason to refuse: the caller asked to go back.
    # A name that is already taken would overwrite the snapshot being restored (two restores in one
    # millisecond), and pruning would then delete the files that restore still needs.
    stamp = int(time.time() * 1000)
    while _manifest_path(store, f"undo-{stamp}").exists():
        stamp += 1
    undo_id = f"undo-{stamp}"
    try:
        snapshot(root, store, undo_id, f"Before undo to {snap_id}")
    except SnapshotError:
        undo_id = None
    restored = removed = unchanged = 0
    try:
        current = {rel: (full, st) for rel, full, st in _scan(root)}
    except SnapshotError:
        current = {}
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
            for n in filenames:
                full = os.path.join(dirpath, n)
                if not os.path.islink(full):
                    current[Path(full).relative_to(root).as_posix()] = (full, os.stat(full))
    for rel, (full, _st) in current.items():
        if rel not in wanted and _inside(root, Path(full).parent):
            os.unlink(full)
            removed += 1
    for rel, entry in wanted.items():
        target = root / rel
        if not _inside(root, target.parent) or target.parent.is_symlink() or target.is_symlink():
            continue  # never write through a link or outside the workspace
        if target.is_file() and target.stat().st_size == entry["size"] and \
                hashlib.sha256(target.read_bytes()).hexdigest() == entry["sha"]:
            unchanged += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.undo-tmp")
        shutil.copyfile(blobs / entry["sha"], tmp)
        os.chmod(tmp, entry["mode"] or 0o644)
        os.replace(tmp, target)
        restored += 1
    return {"snapshot": snap_id, "restored": restored, "removed": removed, "unchanged": unchanged, "undo_snapshot": undo_id}
