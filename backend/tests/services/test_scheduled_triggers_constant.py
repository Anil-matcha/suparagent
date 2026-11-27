"""One list of the triggers whose runs read connected apps like a scheduled run (BL-177): `SCHEDULED_TRIGGERS`.

A literal two-value tuple such as ("schedule", "manual") left in the code would quietly treat a new-mail run as an
outside webhook run (no grants, no uninterrupted access, no draft-only confinement). This fails if one comes back."""
import ast
from pathlib import Path

from app.db.models.routine import ROUTINE_TRIGGERS, SCHEDULED_TRIGGERS

APP = Path(__file__).resolve().parents[2] / "app"
NAMES = {"schedule", "manual"}


def test_the_constant_holds_exactly_the_runs_that_read_apps_like_a_scheduled_one():
    assert set(SCHEDULED_TRIGGERS) == {"schedule", "manual", "app_event"}
    assert set(SCHEDULED_TRIGGERS) <= set(ROUTINE_TRIGGERS) and "webhook" not in SCHEDULED_TRIGGERS and "retry" not in SCHEDULED_TRIGGERS


def test_no_literal_schedule_manual_tuple_remains_outside_the_constants():
    found = []
    for path in APP.rglob("*.py"):
        if path.name == "routine.py" and path.parent.name == "models":
            continue  # where the constants are defined
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and len(node.elts) in (2, 3):
                values = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                # ("schedule", "manual") in any order, alone or with "retry"/"webhook" (a trigger test, not a schema Literal)
                if {"schedule", "manual"} <= values and values <= {"schedule", "manual", "retry", "webhook", "app_event"} and path.name != "workflow.py":
                    found.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert found == [], "use SCHEDULED_TRIGGERS instead of a literal: " + ", ".join(found)


def test_the_run_starts_and_the_bridges_use_the_constant():
    for name in ("services/routine_service.py", "services/composio_bridge.py", "services/thread_runtime.py", "services/routine_uninterrupted.py"):
        assert "SCHEDULED_TRIGGERS" in (APP / name).read_text(encoding="utf-8"), name
