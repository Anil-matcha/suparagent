"""One timeout for every tool call an agent makes through the app.

A stuck connected-app or marketplace tool must not hang a turn: after
`TOOL_CALL_TIMEOUT_SECONDS` the call is cancelled. A read then reports a plain
error; a change (write) is reported as outcome unknown, so the agent does not
repeat it."""


def seconds() -> float:
    from app.core.config import settings
    return float(settings.TOOL_CALL_TIMEOUT_SECONDS)
