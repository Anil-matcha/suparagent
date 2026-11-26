import sqlite3

from tests.db.sqlite_helpers import run_alembic

BEFORE = "d7e4a9c2b638"
AFTER = "e8f5b0d3c749"


def tables(database):
    with sqlite3.connect(database) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_the_table_is_added_empty_and_removed_on_downgrade(tmp_path):
    database = tmp_path / "feedback.db"
    assert run_alembic(database, "upgrade", BEFORE).returncode == 0
    assert "shadow_feedback" not in tables(database)
    result = run_alembic(database, "upgrade", AFTER)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM shadow_feedback").fetchone() == (0,)
        columns = {row[1] for row in db.execute("PRAGMA table_info(shadow_feedback)")}
        assert {"owner_id", "agent_id", "account_id", "thread_id", "kind", "reason", "note", "created_at"} <= columns
    assert run_alembic(database, "downgrade", BEFORE).returncode == 0
    assert "shadow_feedback" not in tables(database)


def test_one_row_per_target_and_rows_go_with_the_agent(tmp_path):
    database = tmp_path / "feedback-unique.db"
    assert run_alembic(database, "upgrade", AFTER).returncode == 0
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("INSERT INTO agent (owner_id,name) VALUES ('o','A')")
        row = "INSERT INTO shadow_feedback (owner_id,agent_id,account_id,thread_id,kind,reason) VALUES ('o',1,'a',?,?, 'wrong_tone')"
        db.execute(row, ("t1", "draft"))
        db.execute(row, ("t1", "ranking"))   # a different kind of target on the same thread
        db.execute(row, ("t2", "draft"))
        try:
            db.execute(row, ("t1", "draft"))
            raise AssertionError("a second row for the same target must be refused")
        except sqlite3.IntegrityError:
            pass
        db.execute("DELETE FROM agent WHERE id=1")
        assert db.execute("SELECT count(*) FROM shadow_feedback").fetchone() == (0,)


def test_upgrading_when_the_table_already_exists_is_safe(tmp_path):
    database = tmp_path / "feedback-twice.db"
    assert run_alembic(database, "upgrade", AFTER).returncode == 0
    assert run_alembic(database, "downgrade", BEFORE).returncode == 0
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE shadow_feedback (id INTEGER PRIMARY KEY)")
    result = run_alembic(database, "upgrade", AFTER)
    assert result.returncode == 0, result.stderr
