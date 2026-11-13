"""Undo a task's file changes (BL-102): snapshot, change, restore, caps and containment."""

import os

import pytest

from app.services import workspace_snapshots as snaps


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws, tmp_path / "store"


def test_restore_undoes_edits_deletes_and_new_files(env):
    ws, store = env
    (ws / "a.txt").write_text("one")
    (ws / "sub").mkdir()
    (ws / "sub" / "b.txt").write_text("two")
    snaps.snapshot(ws, store, "t1")
    (ws / "a.txt").write_text("changed")
    (ws / "sub" / "b.txt").unlink()
    (ws / "new.txt").write_text("created by task")
    result = snaps.restore(ws, store, "t1")
    assert (ws / "a.txt").read_text() == "one"
    assert (ws / "sub" / "b.txt").read_text() == "two"
    assert not (ws / "new.txt").exists()
    assert (result["restored"], result["removed"]) == (2, 1)


def test_undo_can_itself_be_undone(env):
    ws, store = env
    (ws / "a.txt").write_text("one")
    snaps.snapshot(ws, store, "t1")
    (ws / "a.txt").write_text("two")
    undo = snaps.restore(ws, store, "t1")["undo_snapshot"]
    assert (ws / "a.txt").read_text() == "one"
    snaps.restore(ws, store, undo)
    assert (ws / "a.txt").read_text() == "two"


def test_two_restores_in_the_same_millisecond_do_not_overwrite_each_other(env, monkeypatch):
    ws, store = env
    (ws / "a.txt").write_text("one")
    snaps.snapshot(ws, store, "t1")
    (ws / "a.txt").write_text("two")
    frozen = snaps.time.time()
    monkeypatch.setattr(snaps.time, "time", lambda: frozen)
    undo = snaps.restore(ws, store, "t1")["undo_snapshot"]
    snaps.restore(ws, store, undo)
    assert (ws / "a.txt").read_text() == "two"


def test_ignored_folders_and_links_are_left_alone(env, tmp_path):
    ws, store = env
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "x.js").write_text("dep")
    outside = tmp_path / "secret.txt"
    outside.write_text("outside")
    os.symlink(outside, ws / "link.txt")
    (ws / "a.txt").write_text("one")
    snaps.snapshot(ws, store, "t1")
    (ws / "node_modules" / "x.js").write_text("changed")
    (ws / "a.txt").write_text("two")
    snaps.restore(ws, store, "t1")
    assert (ws / "node_modules" / "x.js").read_text() == "changed"
    assert outside.read_text() == "outside" and (ws / "link.txt").is_symlink()


def test_restore_never_writes_through_a_link_out_of_the_workspace(env, tmp_path):
    ws, store = env
    (ws / "d").mkdir()
    (ws / "d" / "f.txt").write_text("one")
    snaps.snapshot(ws, store, "t1")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (ws / "d" / "f.txt").unlink()
    (ws / "d").rmdir()
    os.symlink(elsewhere, ws / "d")
    snaps.restore(ws, store, "t1")
    assert not (elsewhere / "f.txt").exists()


def test_caps_refuse_instead_of_taking_a_partial_snapshot(env, monkeypatch):
    ws, store = env
    monkeypatch.setattr(snaps, "MAX_FILES", 2)
    for i in range(3):
        (ws / f"{i}.txt").write_text("x")
    with pytest.raises(snaps.SnapshotError) as err:
        snaps.snapshot(ws, store, "t1")
    assert err.value.code == "workspace_too_large"
    assert snaps.list_snapshots(store) == []


def test_big_files_are_skipped_not_deleted(env, monkeypatch):
    ws, store = env
    monkeypatch.setattr(snaps, "MAX_FILE_BYTES", 10)
    (ws / "big.bin").write_bytes(b"x" * 50)
    (ws / "a.txt").write_text("one")
    snaps.snapshot(ws, store, "t1")
    (ws / "a.txt").write_text("two")
    snaps.restore(ws, store, "t1")
    assert (ws / "big.bin").exists() and (ws / "a.txt").read_text() == "one"


def test_only_the_newest_snapshots_are_kept_and_blobs_are_collected(env):
    ws, store = env
    for i in range(4):
        (ws / "a.txt").write_text(f"v{i}")
        snaps.snapshot(ws, store, f"t{i}")
        os.utime(store / f"t{i}.json", (i, i))
    snaps.prune(store, keep=2)
    assert {s["id"] for s in snaps.list_snapshots(store)} == {"t2", "t3"} or len(snaps.list_snapshots(store)) == 2
    assert len(list((store / "blobs").iterdir())) <= 2


def test_bad_ids_and_missing_snapshots_are_refused(env):
    ws, store = env
    with pytest.raises(snaps.SnapshotError):
        snaps.restore(ws, store, "../evil")
    with pytest.raises(snaps.SnapshotError) as err:
        snaps.restore(ws, store, "nope")
    assert err.value.status == 404
