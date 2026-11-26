"""Learning from the owner's past replies (BL-174): the opt-in and its bounds, the review store, accept and Undo, forget.

The state lives in a file outside the agent's workspace; nothing here touches a database or Gmail (the staged read
is in test_history_reader.py)."""
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.services import agent_memory, history_learning as hl, local_claude_runner, memory_journal, memory_proposals, memory_topics

AGENT = 4242


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: root)
    return root


def days_ago(n):
    return (datetime.now(UTC) - timedelta(days=n)).date().isoformat()


def enable(**more):
    return hl.update_settings(AGENT, "ca_g", enabled=True, query="label:support", **more)


def lesson(**more):
    body = {"lesson_type": "template", "pattern": "Where is my order", "text": "Thanks for waiting, it ships in two days.",
            "count": 6, "newest": days_ago(3), "oldest": days_ago(40)}
    body.update(more)
    return hl.propose_lesson(AGENT, **body)


# -- the opt-in ---------------------------------------------------------------------------------------------------------

def test_it_is_off_by_default_and_needs_a_support_search():
    assert hl.public_settings("ca_g", hl.settings_for(AGENT, "ca_g"))["enabled"] is False
    with pytest.raises(hl.HistoryRefused) as refused:
        hl.update_settings(AGENT, "ca_g", enabled=True)
    assert refused.value.code == "query_required"


def test_turning_it_on_starts_with_one_stage_and_the_bounds_have_defaults():
    row = enable()
    assert row["enabled"] and row["allowed"] == 30 and row["max_threads"] == 100 and row["months"] == 6
    assert row["threads_read"] == 0 and row["stage_done"] is False and row["finished"] is False


def test_only_the_owner_raising_it_allows_more_and_never_past_the_bound():
    enable(max_threads=60)
    assert hl.update_settings(AGENT, "ca_g", extend=30)["allowed"] == 60
    assert hl.update_settings(AGENT, "ca_g", extend=500)["allowed"] == 60      # capped at the owner's bound
    with pytest.raises(hl.HistoryRefused):
        hl.update_settings(AGENT, "ca_g2", extend=30)                           # not turned on for that account


@pytest.mark.parametrize("bad", [{"max_threads": 10}, {"max_threads": 201}, {"max_threads": True}, {"months": 0}, {"months": 7}])
def test_bounds_outside_the_limits_are_refused(bad):
    with pytest.raises(hl.HistoryRefused):
        hl.update_settings(AGENT, "ca_g", **bad)


def test_not_now_is_remembered_and_turning_on_clears_it():
    row = hl.update_settings(AGENT, "ca_g", declined=True)
    assert row["declined"] is True and row["enabled"] is False
    assert hl.settings_for(AGENT, "ca_g")["declined"] is True       # still there on the next read
    assert enable()["declined"] is False


def test_a_secret_looking_search_is_refused():
    with pytest.raises(hl.HistoryRefused):
        hl.update_settings(AGENT, "ca_g", query="password: hunter2hunter2")


def test_can_read_refuses_until_it_is_on_then_by_stage_and_bound():
    assert hl.check_can_read(AGENT, "ca_g")[1]["code"] == "history_not_enabled"
    enable(max_threads=60)
    assert hl.check_can_read(AGENT, "ca_g")[1] is None
    hl.record_read(AGENT, "ca_g", processed=30, cursor="2026-10-01", digests=[], exhausted=False)
    assert hl.check_can_read(AGENT, "ca_g")[1]["code"] == "history_stage_done"
    hl.update_settings(AGENT, "ca_g", extend=30)
    assert hl.check_can_read(AGENT, "ca_g")[1] is None
    hl.record_read(AGENT, "ca_g", processed=30, cursor="2026-09-01", digests=[], exhausted=False)
    assert hl.check_can_read(AGENT, "ca_g")[1]["code"] == "history_bound_reached"


def test_restart_begins_a_new_one_time_read():
    enable()
    hl.record_read(AGENT, "ca_g", processed=30, cursor="2026-10-01", digests=["a"], exhausted=False)
    row = hl.update_settings(AGENT, "ca_g", restart=True)
    assert row["threads_read"] == 0 and hl.settings_for(AGENT, "ca_g")["seen"] == [] and hl.settings_for(AGENT, "ca_g")["cursor"] is None


# -- where it is kept -----------------------------------------------------------------------------------------------------

def test_the_state_is_stored_outside_the_agent_workspace_and_is_not_listed_or_recalled(workspace_root):
    enable()
    lesson()
    state = hl._path(AGENT)
    assert state.exists() and not str(state).startswith(str(workspace_root))
    home = agent_memory.agent_home(AGENT)
    assert not home.exists() or not any("history" in p.name for p in home.rglob("*"))
    assert memory_topics.list_topics(AGENT) == []                       # nothing is a note before it is accepted


# -- lessons ------------------------------------------------------------------------------------------------------------------

def test_a_lesson_needs_the_opt_in():
    with pytest.raises(hl.HistoryRefused) as refused:
        lesson()
    assert refused.value.code == "history_not_enabled"


def test_a_lesson_is_redacted_bounded_and_listed_as_pending():
    enable()
    made = lesson(pattern="Refund for ORD-889912", text="Email bob@example.com, we refund to card 4111 1111 1111 1111. " + "x" * 700)
    assert "bob@example.com" not in made["text"] and "4111" not in made["text"] and "889912" not in made["pattern"]
    assert len(made["text"]) <= hl.TEXT_MAX
    assert [l["id"] for l in hl.list_lessons(AGENT)["pending"]] == [made["id"]]


@pytest.mark.parametrize("bad, code", [({"lesson_type": "rule"}, "bad_type"), ({"newest": "yesterday"}, "bad_date"),
                                       ({"newest": days_ago(300), "oldest": days_ago(320)}, "too_old"),
                                       ({"oldest": days_ago(1), "newest": days_ago(30)}, "bad_date"), ({"text": "  "}, "empty")])
def test_bad_lessons_are_refused(bad, code):
    enable()
    with pytest.raises(hl.HistoryRefused) as refused:
        lesson(**bad)
    assert refused.value.code == code


def test_a_lesson_that_looks_like_a_secret_is_refused():
    enable()
    with pytest.raises(hl.HistoryRefused) as refused:
        lesson(text="the password: hunter2hunter2 is on the form")
    assert refused.value.code == "looks_secret"


def test_the_pending_cap_and_duplicates_are_enforced():
    enable()
    for n in range(hl.MAX_PENDING):
        lesson(pattern=f"Question number {n}")
    with pytest.raises(hl.HistoryRefused) as refused:
        lesson(pattern="One more question")
    assert refused.value.code == "lesson_limit"
    with pytest.raises(hl.HistoryRefused) as dup:
        lesson(pattern="Question number 3")
    assert dup.value.code == "duplicate"


def test_a_dismissed_lesson_is_never_proposed_again():
    enable()
    made = lesson()
    hl.dismiss_lesson(AGENT, made["id"])
    assert hl.list_lessons(AGENT) == {"pending": [], "accepted": []}
    with pytest.raises(hl.HistoryRefused) as refused:
        lesson()
    assert refused.value.code == "already_decided"
    with pytest.raises(hl.LessonNotFound):
        hl.dismiss_lesson(AGENT, made["id"])


# -- accept, undo, policy, forget ------------------------------------------------------------------------------------------------

def test_accepting_a_template_writes_a_journaled_history_entry_and_undo_restores():
    enable()
    made = lesson()
    accepted = hl.accept_lesson(AGENT, made["id"])
    note = memory_topics.read_topic(AGENT, hl.NOTE)
    assert note.exists and hl.HISTORY_MARK in note.text and "Where is my order" in note.text and "seen 6x" in note.text
    assert accepted["destination"] == "answer-library" and accepted["status"] == "accepted" and accepted["entry_id"]
    assert [row["actor"] for row in memory_journal.read(AGENT)][0] == "history"
    memory_journal.undo(AGENT, accepted["entry_id"])
    assert not memory_topics.read_topic(AGENT, hl.NOTE).exists
    with pytest.raises(hl.LessonNotFound):
        hl.accept_lesson(AGENT, made["id"])


def test_the_owners_edit_is_used_and_redacted():
    enable()
    made = lesson()
    hl.accept_lesson(AGENT, made["id"], text="Call 415 555 0199 and we will fix it.")
    text = memory_topics.read_topic(AGENT, hl.NOTE).text
    assert "we will fix it" in text and "555 0199" not in text


def test_policy_lines_go_only_to_the_suggestion_queue():
    enable()
    made = lesson(lesson_type="policy", pattern="Refund requests", text="Refund offered in 6 of 8 refund requests; 2 declined.")
    accepted = hl.accept_lesson(AGENT, made["id"])
    assert accepted["destination"] == "suggestions"
    assert not memory_topics.read_topic(AGENT, hl.NOTE).exists            # never the answer library, never a rule
    queued = memory_proposals.pending(AGENT)
    assert len(queued) == 1 and "Refund offered in 6 of 8" in queued[0]["text"] and queued[0]["thread_id"] is None
    assert memory_proposals.pending(AGENT)[0]["target"] == agent_memory.MEMORY_FILE   # still needs the owner's Accept there


def test_forget_removes_history_entries_pending_lessons_and_progress_but_keeps_other_notes():
    enable()
    first = lesson()
    hl.accept_lesson(AGENT, first["id"])
    lesson(pattern="Pending one")
    note = memory_topics.read_topic(AGENT, hl.NOTE)
    memory_topics.write_topic(AGENT, hl.NOTE, note.text + "- 2026-10-01 · from chat · the owner's own line\n", expected_hash=note.hash)
    hl.record_read(AGENT, "ca_g", processed=30, cursor="2026-10-01", digests=["a"], exhausted=False)
    gone = hl.forget(AGENT)
    assert gone["entries_removed"] == 1 and gone["lessons_removed"] == 2
    text = memory_topics.read_topic(AGENT, hl.NOTE).text
    assert hl.HISTORY_MARK not in text and "the owner's own line" in text
    row = hl.settings_for(AGENT, "ca_g")
    assert row["enabled"] is False and row["threads_read"] == 0 and row["cursor"] is None and row["query"] == "label:support"
    assert hl.list_lessons(AGENT) == {"pending": [], "accepted": []}
    assert memory_journal.read(AGENT)[0]["actor"] == "history"            # the removal can be undone


def test_a_damaged_state_file_reads_as_empty():
    hl._path(AGENT).parent.mkdir(parents=True, exist_ok=True)
    hl._path(AGENT).write_text("not json", encoding="utf-8")
    assert hl.list_lessons(AGENT) == {"pending": [], "accepted": []}
    assert hl.settings_for(AGENT, "ca_g")["enabled"] is False
    hl._path(AGENT).write_text(json.dumps({"accounts": [], "lessons": {}}), encoding="utf-8")
    assert hl.list_lessons(AGENT) == {"pending": [], "accepted": []}
