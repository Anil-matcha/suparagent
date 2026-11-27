import sqlite3

from tests.db.sqlite_helpers import run_alembic

BEFORE = "18c4d6a2f901"
AFTER = "a4e1c7d93b25"


def test_existing_agents_start_with_no_shares_and_downgrade(tmp_path):
    database = tmp_path / "agent-shares.db"
    result = run_alembic(database, "upgrade", BEFORE)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO agent (owner_id,name) VALUES ('owner','Reader')")
    result = run_alembic(database, "upgrade", AFTER)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT connector_shares FROM agent").fetchone() == ("{}",)
    result = run_alembic(database, "downgrade", BEFORE)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        assert "connector_shares" not in {row[1] for row in db.execute("PRAGMA table_info(agent)")}


def test_upgrading_when_the_column_already_exists_is_safe(tmp_path):
    database = tmp_path / "agent-shares-twice.db"
    assert run_alembic(database, "upgrade", BEFORE).returncode == 0
    with sqlite3.connect(database) as db:
        db.execute("ALTER TABLE agent ADD COLUMN connector_shares JSON NOT NULL DEFAULT '{}'")
    result = run_alembic(database, "upgrade", AFTER)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        assert [row[1] for row in db.execute("PRAGMA table_info(agent)")].count("connector_shares") == 1
