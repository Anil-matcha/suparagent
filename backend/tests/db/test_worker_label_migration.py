import sqlite3

from tests.db.sqlite_helpers import run_alembic

BEFORE = "b5c2e7a914d3"
AFTER = "c6d3f8b1a527"


def tables(database):
    with sqlite3.connect(database) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_both_tables_are_added_empty_and_removed_on_downgrade(tmp_path):
    database = tmp_path / "labels.db"
    assert run_alembic(database, "upgrade", BEFORE).returncode == 0
    assert not {"worker_label", "worker_label_action"} & tables(database)
    result = run_alembic(database, "upgrade", AFTER)
    assert result.returncode == 0, result.stderr
    assert {"worker_label", "worker_label_action"} <= tables(database)
    assert run_alembic(database, "downgrade", BEFORE).returncode == 0
    assert not {"worker_label", "worker_label_action"} & tables(database)


def test_a_label_name_is_unique_per_account_and_actions_go_with_the_agent(tmp_path):
    database = tmp_path / "labels-unique.db"
    assert run_alembic(database, "upgrade", AFTER).returncode == 0
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("INSERT INTO worker_label (owner_id,account_id,name,gmail_label_id) VALUES ('o','a','Worker/Urgent','L1')")
        db.execute("INSERT INTO worker_label (owner_id,account_id,name,gmail_label_id) VALUES ('o','b','Worker/Urgent','L9')")
        try:
            db.execute("INSERT INTO worker_label (owner_id,account_id,name,gmail_label_id) VALUES ('o','a','Worker/Urgent','L2')")
            raise AssertionError("the same name twice in one account must be refused")
        except sqlite3.IntegrityError:
            pass
        db.execute("INSERT INTO agent (owner_id,name) VALUES ('o','A')")
        db.execute("INSERT INTO worker_label_action (owner_id,agent_id,account_id,thread_id,label_name,gmail_label_id,status) VALUES ('o',1,'a','t','Worker/Urgent','L1','applied')")
        db.execute("DELETE FROM agent WHERE id=1")
        assert db.execute("SELECT count(*) FROM worker_label_action").fetchone() == (0,)
