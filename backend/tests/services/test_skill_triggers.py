"""BL-116: optional trigger phrases on skills, and the pure suggestion matcher."""

import pytest

from app.services import agent_skills as skills
from app.services import local_claude_runner


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    return tmp_path


def code(call, *args, **kwargs):
    with pytest.raises(skills.SkillError) as caught:
        call(*args, **kwargs)
    return caught.value.code


def test_triggers_are_normalized_and_capped():
    assert skills.normalize_triggers([" Weekly  REPORT ", "weekly report", "", "q3 review"]) == ["weekly report", "q3 review"]
    assert skills.normalize_triggers("a b c | Expense Check") == ["a b c", "expense check"]
    assert skills.normalize_triggers(None) == []
    assert code(skills.normalize_triggers, ["ab"]) == "invalid_triggers"
    assert code(skills.normalize_triggers, ["x" * 61]) == "invalid_triggers"
    assert code(skills.normalize_triggers, [f"phrase {i}" for i in range(9)]) == "invalid_triggers"
    assert code(skills.normalize_triggers, [5]) == "invalid_triggers"
    assert skills.normalize_triggers(["sk-" + "a" * 30]) == []


def test_triggers_are_stored_with_the_skill_and_kept_on_edit():
    saved = skills.write_skill(1, "weekly-report", "Summarise", "Steps.", triggers=["Weekly report", "week summary"])
    assert saved.triggers == ["weekly report", "week summary"]
    assert skills.read_skill(1, "weekly-report").triggers == ["weekly report", "week summary"]
    # None keeps them; a list replaces them; an empty list clears them.
    assert skills.write_skill(1, "weekly-report", "Summarise 2", "Steps.").triggers == ["weekly report", "week summary"]
    assert skills.write_skill(1, "weekly-report", "Summarise 2", "Steps.", triggers=["status"]).triggers == ["status"]
    assert skills.write_skill(1, "weekly-report", "Summarise 2", "Steps.", triggers=[]).triggers == []


def test_a_skill_without_triggers_has_no_header_line(home):
    skills.write_skill(1, "plain", "A skill", "Steps.")
    assert "triggers" not in (home / "agents" / "1" / "skills" / "plain" / "SKILL.md").read_text()
    assert skills.read_skill(1, "plain").triggers == []


def test_bad_triggers_in_a_hand_edited_file_are_skipped(home):
    skills.write_skill(1, "hand", "A skill", "Steps.")
    path = home / "agents" / "1" / "skills" / "hand" / "SKILL.md"
    path.write_text(path.read_text().replace("source: user\n", "source: user\ntriggers: ok phrase | x | " + "y" * 90 + " | OK Phrase\n"))
    assert skills.read_skill(1, "hand").triggers == ["ok phrase"]


def test_suggest_matches_whole_phrases_for_enabled_skills_only():
    a = skills.Skill("weekly-report", "d", "b", triggers=["weekly report"])
    b = skills.Skill("expenses", "d", "b", triggers=["expense", "expense check"])
    off = skills.Skill("off-skill", "d", "b", enabled=False, triggers=["weekly report"])
    none = skills.Skill("plain", "d", "b")
    pool = [a, b, off, none]
    assert [s.name for s in skills.suggest(pool, "Please write the  Weekly   Report now")] == ["weekly-report"]
    assert [s.name for s in skills.suggest(pool, "run an expense check and weekly report")] == ["expenses", "weekly-report"]
    assert skills.suggest(pool, "expenses are high") == []  # whole words only
    assert skills.suggest(pool, "") == [] and skills.suggest(pool, "nothing here") == []
    many = [skills.Skill(f"s{i}", "d", "b", triggers=["go now"]) for i in range(6)]
    assert len(skills.suggest(many, "go now")) == skills.MAX_PER_MESSAGE


def test_suggest_never_changes_anything():
    s = skills.Skill("weekly-report", "d", "b", enabled=True, triggers=["weekly report"])
    skills.suggest([s], "weekly report")
    assert s.enabled is True and s.body == "b"
    assert skills.prompt_block([]) == ""


def test_export_includes_triggers():
    skills.write_skill(1, "weekly-report", "Summarise", "Steps.", triggers=["weekly report"])
    skills.write_skill(1, "plain", "Plain", "Steps.")
    items = {i["name"]: i for i in skills.export_items(1)}
    assert items["weekly-report"]["triggers"] == ["weekly report"]
    assert "triggers" not in items["plain"]
