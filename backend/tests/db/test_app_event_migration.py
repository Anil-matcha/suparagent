import sqlite3

from tests.db.sqlite_helpers import run_alembic

BEFORE = "c6d3f8b1a527"
AFTER = "d7e4a9c2b638"
NEW = {"app_event_trigger", "app_event_receipt", "app_event_day"}


def tables(database):
    with sqlite3.connect(database) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def routine_columns(database):
    with sqlite3.connect(database) as db:
        return {row[1] for row in db.execute("PRAGMA table_info(routine)")}


def test_existing_routines_have_no_event_trigger_and_downgrade_restores(tmp_path):
    database = tmp_path / "events.db"
    assert run_alembic(database, "upgrade", BEFORE).returncode == 0
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO agent (owner_id,name) VALUES ('o','A')")
        db.execute("INSERT INTO routine (owner_id,agent_id,name,prompt,schedule_kind,schedule_spec) VALUES ('o',1,'r','p','manual','{}')")
    assert not NEW & tables(database) and "app_event" not in routine_columns(database)
    result = run_alembic(database, "upgrade", AFTER)
    assert result.returncode == 0, result.stderr
    assert NEW <= tables(database)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT app_event FROM routine").fetchone() == (None,)
    assert run_alembic(database, "downgrade", BEFORE).returncode == 0
    assert not NEW & tables(database) and "app_event" not in routine_columns(database)


def test_one_trigger_per_routine_receipts_dedupe_and_cascade(tmp_path):
    database = tmp_path / "events-unique.db"
    assert run_alembic(database, "upgrade", AFTER).returncode == 0
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("INSERT INTO agent (owner_id,name) VALUES ('o','A')")
        db.execute("INSERT INTO routine (owner_id,agent_id,name,prompt,schedule_kind,schedule_spec) VALUES ('o',1,'r','p','manual','{}')")
        db.execute("INSERT INTO app_event_trigger (owner_id,routine_id,composio_trigger_id,composio_account_id,slug) VALUES ('o',1,'t1','a','GMAIL_NEW_GMAIL_MESSAGE')")
        for bad in ("INSERT INTO app_event_trigger (owner_id,routine_id,composio_trigger_id,composio_account_id,slug) VALUES ('o',1,'t2','a','S')",
                    "INSERT INTO app_event_trigger (owner_id,routine_id,composio_trigger_id,composio_account_id,slug) VALUES ('o',1,'t1','a','S')"):
            try:
                db.execute(bad)
                raise AssertionError("a second trigger for a routine, or a repeated remote id, must be refused")
            except sqlite3.IntegrityError:
                pass
        db.execute("INSERT INTO app_event_receipt (trigger_id,event_hash) VALUES (1,'h')")
        try:
            db.execute("INSERT INTO app_event_receipt (trigger_id,event_hash) VALUES (1,'h')")
            raise AssertionError("a repeated event hash must be refused")
        except sqlite3.IntegrityError:
            pass
        db.execute("INSERT INTO app_event_day (trigger_id,day) VALUES (1,'2026-11-26')")
        assert db.execute("SELECT received,dropped_cap FROM app_event_day").fetchone() == (0, 0)
        db.execute("DELETE FROM routine WHERE id=1")
        assert db.execute("SELECT (SELECT count(*) FROM app_event_trigger),(SELECT count(*) FROM app_event_receipt),(SELECT count(*) FROM app_event_day)").fetchone() == (0, 0, 0)


def test_one_queued_wake_per_routine_and_time_slot(tmp_path):
    database = tmp_path / "events-slot.db"
    assert run_alembic(database, "upgrade", AFTER).returncode == 0
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO agent (owner_id,name) VALUES ('o','A')")
        db.execute("INSERT INTO routine (owner_id,agent_id,name,prompt,schedule_kind,schedule_spec) VALUES ('o',1,'r','p','manual','{}')")
        row = "INSERT INTO routine_run (routine_id,trigger,state,scheduled_for) VALUES (1,?,'queued','2026-11-26 09:00:00')"
        db.execute(row, ("app_event",))
        db.execute(row, ("manual",))  # the slot rule applies to new-mail wakes only
        db.execute(row, ("manual",))
        try:
            db.execute(row, ("app_event",))
            raise AssertionError("two new-mail wakes in one slot must be refused")
        except sqlite3.IntegrityError:
            pass
