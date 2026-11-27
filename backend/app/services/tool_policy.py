"""Every automatic tool decision passes through the durable approval gate."""
from pathlib import Path

from app.computers import policy as computers
from app import composio_generic
from app.services import composio_policy, safety_floor
from app.team import policy as teams

FILE_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def _inside(path, workspace):
    try:
        Path(path).resolve().relative_to(Path(workspace).resolve())
    except (ValueError, OSError):
        return False
    return True


def _full_access(tool, run, arguments):
    """Full access: the engine's own commands and workspace edits run without a card.

    The decision stays here, not in the CLI, so the approval log records it and the safety
    floor still applies. A command on the floor or one that cannot be read, and a write outside
    the agent's workspace, still wait for a person (or are refused when nobody can answer).
    Connected apps, marketplace tools, screen input and team tools never reach this function.
    """
    if not isinstance(arguments, dict):
        return None
    if tool == "Bash":
        command = arguments.get("command")
        return ("allowed", "full_access") if isinstance(command, str) and safety_floor.clears_floor(command) else None
    if tool == "apply_patch":
        changes, workspace = arguments.get("changes"), getattr(run.ctx, "workspace", None)
        paths = [c.get("path") for c in changes if isinstance(c, dict)] if isinstance(changes, list) else []
        if not paths or workspace is None or arguments.get("grant_root") or not all(isinstance(p, str) and _inside(p, workspace) for p in paths):
            return None
        return "allowed", "full_access"
    if tool in FILE_WRITE_TOOLS:
        target, workspace = arguments.get("file_path") or arguments.get("notebook_path"), getattr(run.ctx, "workspace", None)
        if not isinstance(target, str) or workspace is None:
            return None
        return ("allowed", "full_access") if _inside(target, workspace) else None
    return None


def automatic(tool, mode, run, arguments):
    if getattr(run.ctx, "media_review_only", False):
        return "denied", "media_review_only"
    if getattr(run.ctx, "draft_only", False) and not tool.startswith(composio_policy.PREFIX):
        return "denied", "draft_only_run"
    if tool.startswith('mcp__pages__'):
        # Only renders a sandboxed page in the chat (pages_bridge.py); never in a draft-only run (refused above).
        return ('allowed', 'pages_show') if tool in ('mcp__pages__show_page', 'mcp__pages__update_page') else ('denied', 'unknown_pages_tool')
    if tool.startswith('mcp__memory__'):
        # Read-only search of the agent's own history (memory_bridge.py).
        return ('allowed', 'memory_read') if tool in ('mcp__memory__session_search', 'mcp__memory__session_read') else ('denied', 'unknown_memory_tool')
    if tool.startswith('mcp__team__'):
        return teams.automatic(tool, mode, run.ctx.team, arguments)
    if tool.startswith('mcp__community__'):
        # Marketplace tools have unknown effects. They are never automatically
        # allowed, and plan mode cannot execute them even if a card is answered.
        if tool.removeprefix('mcp__community__') not in getattr(run.ctx, 'community_tools', []):
            return 'denied', 'marketplace_tool_not_granted'
        if tool == 'mcp__community__custom_search':
            # Searches only the index of tools this agent was already granted; nothing leaves the backend.
            return 'allowed', 'community_search'
        bridge = getattr(run.ctx, 'community_bridge', None)
        if bridge is not None:
            # A call naming a mailbox or number the agent was not given never reaches an approval card.
            reason = bridge.argument_refusal(tool.removeprefix('mcp__community__'), arguments)
            if reason is not None:
                return 'denied', reason
        return ('denied', 'plan_mode') if mode == 'plan' else None
    if tool.startswith(composio_policy.PREFIX):
        # Connected apps (composio_bridge.py): decided here, never by a saved command rule.
        grants = getattr(run, "routine_grants", None)
        if grants is not None:
            name = tool.removeprefix(composio_policy.PREFIX)
            from app import composio_tools
            found = composio_tools.BY_NAME.get(name)
            if name not in grants or found is None:
                return "denied", "routine_action_not_granted"
            if found.kind == "write":
                if name not in composio_tools.ROUTINE_WRITE_TOOLS or not composio_tools.allows((run.ctx.composio_apps or {}).get(found.app), "write"):
                    return "denied", "routine_write_not_granted"
                if mode == "plan":
                    return "denied", "plan_mode"
                return "allowed", "routine_write_grant"
            if found.kind == "label":
                # A routine may add its own labels only when its grants name the tool and the agent is at the labels level or above.
                if name not in composio_tools.ROUTINE_WRITE_TOOLS or not composio_tools.allows((run.ctx.composio_apps or {}).get(found.app), "label"):
                    return "denied", "routine_label_not_granted"
                if mode == "plan":
                    return "denied", "plan_mode"
                return "allowed", "routine_label_grant"
        decision = composio_policy.automatic(tool, mode, run.ctx.composio_apps, arguments,
            getattr(run.ctx, "composio_first_use_hit", False),
            uninterrupted=getattr(run, "uninterrupted_access", False))
        if tool == composio_policy.PREFIX + composio_generic.RUN:
            # A generic call decided without a card is remembered, so the bridge checks it again before it runs.
            key, calls = composio_generic.call_key(arguments), getattr(run.ctx, "composio_auto_calls", None)
            if calls is not None:
                if decision is not None and decision[0] == "allowed":
                    calls.add(key)
                else:
                    calls.discard(key)
        return decision
    if mode == "full_access" and not tool.startswith("mcp__"):
        return _full_access(tool, run, arguments)
    # Screen grants are turn scoped and are never persisted as command rules.
    if tool in computers.SCREEN_INPUT or tool in computers.SCREEN_READ:
        return computers.automatic(tool, mode, screen_granted=getattr(run, "screen_granted", False))
    return computers.automatic(tool, mode)
