import json
import os
import time

import pytest

from app import composio_mcp_server
from app.core.config import Settings
from app.services import large_tool_results as ltr


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(ltr.tenant_paths, "root", lambda: tmp_path)
    return tmp_path


def big(n=60000):
    return {"rows": [{"id": i, "note": "row-%d" % i} for i in range(n // 20)]}


def test_small_and_error_results_pass_through():
    small = {"ok": True}
    assert ltr.shrink("o", 1, small) is small
    err = {"error": "x" * 100000, "code": "app_error"}
    assert ltr.shrink("o", 1, err) is err


def test_large_result_becomes_preview_and_is_readable():
    out = ltr.shrink("o", 1, big())
    assert out["large_result"] and out["saved_ref"] and len(json.dumps(out)) < 6000
    assert out["shape"].startswith("object with keys: rows")
    first = ltr.read("o", 1, {"ref": out["saved_ref"], "offset": 0, "length": 100})
    assert first["text"] == out["preview"][:100] and first["next_offset"] == 100
    found = ltr.read("o", 1, {"ref": out["saved_ref"], "query": "ROW-777"})
    assert found["matches"] and "row-777" in found["matches"][0]["text"]
    tail = ltr.read("o", 1, {"ref": out["saved_ref"], "offset": first["total_chars"] - 5, "length": 50})
    assert tail["next_offset"] is None and len(tail["text"]) == 5


def test_reads_are_bounded_and_validated():
    ref = ltr.shrink("o", 1, big())["saved_ref"]
    assert len(ltr.read("o", 1, {"ref": ref, "length": 10**6})["text"]) == ltr.MAX_READ_CHARS
    assert ltr.read("o", 1, {"ref": ref, "offset": -1})["code"] == "bad_arguments"
    assert ltr.read("o", 1, {"ref": ref, "query": " "})["code"] == "bad_arguments"
    many = ltr.read("o", 1, {"ref": ref, "query": "row"})
    assert len(many["matches"]) == ltr.MAX_MATCHES and many["more"] is True


def test_scoped_to_owner_and_thread_and_refs_are_not_paths():
    ref = ltr.shrink("alice", 7, big())["saved_ref"]
    assert ltr.read("bob", 7, {"ref": ref})["code"] == "not_found"
    assert ltr.read("alice", 8, {"ref": ref})["code"] == "not_found"
    for bad in ("../../etc/passwd", "x" * 16, "", 5, None):
        assert ltr.read("alice", 7, {"ref": bad})["code"] == "not_found"


def test_secrets_are_redacted_in_the_saved_copy(home):
    data = big()
    data["rows"][0]["note"] = "key sk-ant-api03-abcdefghijklmnopqrstuvwx1234567890"
    ref = ltr.shrink("o", 1, data)["saved_ref"]
    text = ltr.read("o", 1, {"ref": ref, "length": 8000})["text"]
    assert "abcdefghijklmnopqrstuvwx1234567890" not in text
    saved = next(home.rglob(ref + ".json")).read_text()
    assert "abcdefghijklmnopqrstuvwx1234567890" not in saved


def test_saved_files_are_private_and_expire(home):
    ref = ltr.shrink("o", 1, big())["saved_ref"]
    path = next(home.rglob(ref + ".json"))
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    later = time.time() + ltr.TTL_SECONDS + 5
    assert ltr.read("o", 1, {"ref": ref}, now=later)["code"] == "expired"
    assert not path.exists()
    ref2 = ltr.shrink("o", 1, big())["saved_ref"]
    old = next(home.rglob(ref2 + ".json"))
    os.utime(old, (time.time() - ltr.TTL_SECONDS - 10,) * 2)
    ltr.shrink("o", 1, big())
    assert not old.exists()


def test_file_count_per_thread_is_capped(home, monkeypatch):
    monkeypatch.setattr(ltr, "MAX_FILES_PER_THREAD", 3)
    for _ in range(6):
        ltr.shrink("o", 1, big())
    assert len(list(home.rglob("*.json"))) <= 3


def test_stored_size_cap_marks_truncation(monkeypatch):
    monkeypatch.setattr(ltr, "MAX_STORED_CHARS", 30000)
    out = ltr.shrink("o", 1, big(200000))
    assert out["saved_truncated"] is True
    assert ltr.read("o", 1, {"ref": out["saved_ref"], "offset": 0})["total_chars"] == 30000


def test_unwritable_store_still_returns_a_short_preview(home, monkeypatch):
    monkeypatch.setattr(ltr.os, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    out = ltr.shrink("o", 1, big())
    assert out["large_result"] and "saved_ref" not in out and len(json.dumps(out)) < 6000


def test_threshold_setting_has_bounds(monkeypatch):
    assert Settings.model_fields["LARGE_TOOL_RESULT_CHARS"].default == 20000
    for bad in (100, 10**7):
        with pytest.raises(ValueError):
            Settings(LARGE_TOOL_RESULT_CHARS=bad)


def test_mcp_server_offers_the_read_tool_only_with_app_tools(tmp_path):
    server = composio_mcp_server.Server(tmp_path, {"gmail": "read"})
    assert "read_saved_result" in server.names
    assert "read_saved_result" not in composio_mcp_server.Server(tmp_path, {}).names
