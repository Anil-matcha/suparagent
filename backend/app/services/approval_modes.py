"""Approval modes: how much a conversation may do before it asks.

A mode is a name the person picks; the Claude CLI's own permission mode does
the enforcing, so this app never re-implements what the CLI already decides.

    ask          every call the CLI would ask about waits for Allow or Deny
    accept_edits file edits inside the workspace go through; the rest still asks
    approve_for_me the engine's native safety reviewer handles eligible requests
    plan         read-only: the CLI plans and edits nothing
    full_access  commands and edits run without asking (Claude and Codex), for work nobody is
                 there to answer, such as routines. Opt-in per agent, never the default.
                 The safety floor, connected-app and marketplace grants, sign-ins and
                 call-time revocation still apply; see tool_policy.automatic.

`mode=None` means "not chosen" and behaves exactly as before modes existed (ask).
"""

DEFAULT_MODE = "ask"
MODES = ("ask", "accept_edits", "approve_for_me", "plan", "full_access")
NATIVE_REVIEW_ENGINES = frozenset({"claude", "codex", "cursor"})
FULL_ACCESS_ENGINES = frozenset({"claude", "codex"})

# The CLI's --permission-mode value for each mode.
_CLI_MODE = {
    "ask": "default",
    "accept_edits": "acceptEdits",
    "approve_for_me": "auto",
    "plan": "plan",
    # The app, not the CLI, allows the calls: they still reach the permission prompt so the
    # safety floor and the approval log apply (tool_policy.automatic).
    "full_access": "default",
}


class UnknownApprovalMode(ValueError):
    pass


def validate_mode(mode: str | None) -> str:
    """The mode, or the default for None. Raises UnknownApprovalMode."""
    if mode is None:
        return DEFAULT_MODE
    if mode not in MODES:
        raise UnknownApprovalMode(f"approval mode must be one of: {', '.join(MODES)}")
    return mode


def cli_permission_mode(mode: str | None) -> str:
    """The value for the CLI's `--permission-mode`."""
    return _CLI_MODE[validate_mode(mode)]


def modes_for_engine(engine: str | None) -> tuple[str, ...]:
    """Modes this engine can enforce natively; unsupported modes run as Ask."""
    return tuple(
        mode for mode in MODES
        if (mode != "approve_for_me" or engine in NATIVE_REVIEW_ENGINES)
        and (mode != "full_access" or engine in FULL_ACCESS_ENGINES)
    )
