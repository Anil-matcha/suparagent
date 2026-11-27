import sqlite3

from tests.db.sqlite_helpers import run_alembic

BEFORE = "e8f5b0d3c749"
AFTER = "a1c7e3d9b542"


def tables(database):
    with sqlite3.connect(database) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_the_table_is_added_empty_and_removed_on_downgrade(tmp_path):
    database = tmp_path / "apps.db"
    assert run_alembic(database, "upgrade", BEFORE).returncode == 0
    assert "saved_app" not in tables(database)
    result = run_alembic(database, "upgrade", AFTER)
    assert result.returncode == 0, result.stderr
    assert "saved_app" in tables(database)
    assert run_alembic(database, "downgrade", BEFORE).returncode == 0
    assert "saved_app" not in tables(database)


def test_one_app_per_owner_and_page_and_it_has_no_link_to_the_chat(tmp_path):
    database = tmp_path / "apps-unique.db"
    assert run_alembic(database, "upgrade", AFTER).returncode == 0
    row = "INSERT INTO saved_app (id,owner_id,page_id,name,versions,uses) VALUES (?,?,?,?,'[]','[]')"
    with sqlite3.connect(database) as db:
        db.execute(row, ("sa_aaaaaaaaaaaaaaaa", "o", "p", "n"))
        db.execute(row, ("sa_bbbbbbbbbbbbbbbb", "other", "p", "n"))
        try:
            db.execute(row, ("sa_cccccccccccccccc", "o", "p", "n"))
            raise AssertionError("the same page twice for one owner must be refused")
        except sqlite3.IntegrityError:
            pass
        assert db.execute("SELECT count(*) FROM pragma_foreign_key_list('saved_app')").fetchone() == (0,)
