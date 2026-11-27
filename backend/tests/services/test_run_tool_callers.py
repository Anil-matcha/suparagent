"""No caller may forget the share of mailbox rule: a call without `shares=` would silently run unfiltered."""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"


def test_every_run_tool_call_passes_shares():
    missing, seen = [], 0
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            target = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if target != "run_tool":
                continue
            seen += 1
            if "shares" not in {keyword.arg for keyword in node.keywords}:
                missing.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert seen >= 6, "the call sites moved: update this test"
    assert missing == [], "run_tool without shares=: " + ", ".join(missing)
