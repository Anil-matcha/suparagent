import sqlite3
from pathlib import Path

import pytest

from app.server import backup


def _setup_fake_data_dir(base: Path, monkeypatch) -> dict[str, Path]:
    from app.server import database

    db_path = base / "suparagent.db"
    ws_dir = base / "local"
    att_dir = base / "attachments"
    dec_dir = base / "decisions"
    mem_dir = base / "memory-state"
    ws_dir.mkdir(parents=True)
    att_dir.mkdir(parents=True)
    dec_dir.mkdir(parents=True)
    mem_dir.mkdir(parents=True)
    (ws_dir / "hello.txt").write_text("workspace content", encoding="utf-8")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (database.newest_revision(),))
        conn.execute("CREATE TABLE agent (id INTEGER PRIMARY KEY, workspace_path TEXT)")
        conn.execute("CREATE TABLE thread (id INTEGER PRIMARY KEY, workspace_path TEXT)")
        conn.execute("CREATE TABLE turn (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE room (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO agent (id, workspace_path) VALUES (1, ?)", (str(ws_dir / "agents" / "1"),))
        conn.commit()
    finally:
        conn.close()

    where = {
        "database": db_path,
        "attachments": att_dir,
        "decisions": dec_dir,
        "memory-state": mem_dir,
        "workspace": ws_dir,
    }
    monkeypatch.setattr(backup, "locations", lambda: where)
    monkeypatch.setattr(backup, "_server_running", lambda _db: False)
    return where


def test_estimate_preview_and_staged_restore_roundtrip(tmp_path, monkeypatch):
    source_dir = tmp_path / "src"
    where = _setup_fake_data_dir(source_dir, monkeypatch)
    out_dir = tmp_path / "exports"

    est = backup.estimate_backup()
    assert est["database_exists"] is True
    assert est["total_files"] >= 2

    report = backup.create_backup(out_dir=out_dir, passphrase="correct-horse-battery")
    assert report.archive.is_file()

    with pytest.raises(backup.BackupError):
        backup.preview_backup(report.archive, "wrong-passphrase")

    summary = backup.preview_backup(report.archive, "correct-horse-battery")
    assert summary["encrypted"] is True
    assert summary["compatible"] is True
    assert summary["counts"]["agents"] == 1

    # Stage restore on the same directory after modifying workspace content, then apply cleanly
    (where["workspace"] / "hello.txt").write_text("modified before restore", encoding="utf-8")
    backup.stage_pending_restore(report.archive, source_dir, "correct-horse-battery")
    assert (source_dir / backup.PENDING_RESTORE_DIR / "archive.tar.gz").is_file()

    restored = backup.apply_pending_restore(source_dir, out_dir)
    assert restored is not None
    assert (where["workspace"] / "hello.txt").read_text(encoding="utf-8") == "workspace content"
    assert not (source_dir / backup.PENDING_RESTORE_DIR).exists()
