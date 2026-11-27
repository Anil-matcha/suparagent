import sqlite3

from tests.db.sqlite_helpers import run_alembic

BEFORE = "a4e1c7d93b25"
AFTER = "b5c2e7a914d3"


def tables(database):
    with sqlite3.connect(database) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_the_table_is_added_empty_and_removed_on_downgrade(tmp_path):
    database = tmp_path / "shadow.db"
    assert run_alembic(database, "upgrade", BEFORE).returncode == 0
    assert "shadow_prediction" not in tables(database)
    result = run_alembic(database, "upgrade", AFTER)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM shadow_prediction").fetchone() == (0,)
        columns = {row[1] for row in db.execute("PRAGMA table_info(shadow_prediction)")}
        assert {"owner_id", "agent_id", "account_id", "thread_id", "rank", "category", "owner_only", "draft_ref",
                "draft_digest", "draft_text", "turn_id", "predicted_at"} <= columns
    assert run_alembic(database, "downgrade", BEFORE).returncode == 0
    assert "shadow_prediction" not in tables(database)


def test_one_mark_per_agent_account_thread_and_turn(tmp_path):
    database = tmp_path / "shadow-unique.db"
    assert run_alembic(database, "upgrade", AFTER).returncode == 0
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("INSERT INTO agent (owner_id,name) VALUES ('o','A')")
        row = "INSERT INTO shadow_prediction (owner_id,agent_id,account_id,thread_id,rank,category,turn_id) VALUES ('o',1,'a','t',1,'bug',?)"
        db.execute(row, (7,))
        db.execute(row, (8,))  # a later turn's view of the same thread is a new row
        try:
            db.execute(row, (7,))
            raise AssertionError("a second mark in the same turn must be refused")
        except sqlite3.IntegrityError:
            pass
        db.execute("DELETE FROM agent WHERE id=1")  # the predictions go with the agent
        assert db.execute("SELECT count(*) FROM shadow_prediction").fetchone() == (0,)


def test_upgrading_when_the_table_already_exists_is_safe(tmp_path):
    database = tmp_path / "shadow-twice.db"
    assert run_alembic(database, "upgrade", AFTER).returncode == 0
    assert run_alembic(database, "downgrade", BEFORE).returncode == 0
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE shadow_prediction (id INTEGER PRIMARY KEY)")
    result = run_alembic(database, "upgrade", AFTER)
    assert result.returncode == 0, result.stderr
