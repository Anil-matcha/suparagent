"""Per-agent tool scope validation, matching, and optional tool group registry."""

from __future__ import annotations

import fnmatch
import re

_RULE_PATTERN = re.compile(r"^(native|mcp):[A-Za-z0-9_.:*-]{1,120}$")

# Optional built-in tool groups that an agent can disable to reduce prompt/context bloat.
OPTIONAL_TOOL_GROUPS: dict[str, dict] = {
    "images": {"label": "Image generation", "description": "Create or edit images from prompts.", "approx_tokens": 450},
    "speech": {"label": "Voice & audio", "description": "Text-to-speech and audio synthesis helpers.", "approx_tokens": 350},
    "computers": {"label": "Remote computers", "description": "Desktop/VM computer actions.", "approx_tokens": 600},
    "memory_tools": {"label": "Memory recall tools", "description": "Structured past-conversation lookup tools.", "approx_tokens": 400},
}


def validate_tool_scope(raw: object) -> dict | None:
    """Validate `{allow?: list[str], deny?: list[str]}` rules; returns normalized dict or None."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("tool_scope must be an object with optional 'allow' and 'deny' lists.")
    unknown_keys = set(raw.keys()) - {"allow", "deny"}
    if unknown_keys:
        raise ValueError(f"tool_scope has unsupported key(s): {', '.join(sorted(unknown_keys))}")

    out: dict[str, list[str]] = {}
    for key in ("allow", "deny"):
        if key not in raw or raw[key] is None:
            continue
        items = raw[key]
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError(f"tool_scope.{key} must be a list of at most 100 rule patterns.")
        cleaned: list[str] = []
        for entry in items:
            if not isinstance(entry, str) or not _RULE_PATTERN.fullmatch(entry.strip()):
                raise ValueError(f"Invalid tool rule {entry!r}; use native:<tool> or mcp:<app>:<tool>.")
            rule = entry.strip()
            if rule not in cleaned:
                cleaned.append(rule)
        out[key] = cleaned
    return out if out else None


def validate_tools_off(raw: object) -> list[str]:
    """Validate optional tool group disables."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("tools_off must be a list of tool group identifiers.")
    cleaned: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or entry not in OPTIONAL_TOOL_GROUPS:
            raise ValueError(f"Unknown tool group: {entry!r}")
        if entry not in cleaned:
            cleaned.append(entry)
    return cleaned


def tool_allowed(scope: dict | None, qualified_name: str) -> bool:
    """Evaluate `qualified_name` (e.g. `native:Bash`, `mcp:composio:gmail_send`) against `tool_scope`.
    `deny` always wins; if `allow` is present, the tool must match at least one allow rule."""
    if not isinstance(scope, dict):
        return True
    deny = scope.get("deny")
    if isinstance(deny, list):
        for pattern in deny:
            if isinstance(pattern, str) and fnmatch.fnmatchcase(qualified_name, pattern):
                return False
    allow = scope.get("allow")
    if isinstance(allow, list):
        return any(isinstance(pattern, str) and fnmatch.fnmatchcase(qualified_name, pattern) for pattern in allow)
    return True
