"""`request_app_connection`: any agent asks the person to connect an app from the chat.

An agent that needs an app it has no tools for (Gmail, GitHub, a calendar...) used to ask the
owner where to find the data and stop. Now it calls this tool, which checks the three things only
the person can supply and records one card in the conversation:

  needs_key         connected apps are not set up on this computer yet (no Composio key)
  needs_connection  no active account of the app exists
  needs_access      an account exists, but this agent may not use the app yet
  ready             the account exists and the agent may use it (tools mount on its next turn)

The tool lives in the connected-apps server (composio_bridge.py), which every person-started turn
can mount, so it does not depend on the agent being in a team. It never connects, grants or
stores anything and never takes a credential: the card sends the person to the app's own
sign-in, and access stays the person's decision in the agent's settings. The result tells the
agent to end its turn; the person's reply resumes it.
"""
from app import composio_generic, composio_tools
from app.db.models import Agent
from app.services import composio_service
from app.services.composio_service import UserError
from app.team import fence

EVENT = "app.connection.requested"
REASON_MAX = 200

NEXT = {
    "needs_key": "Connected apps are not set up yet. A card tells the person where to add their key. Never ask for a key, "
                 "token or password in the chat. End your turn now and wait; do not do the work another way.",
    "needs_connection": "A card in the chat lets the person sign in to {app}. End your turn now and wait; you are "
                        "continued when they say it is done. Do not ask where else to find the data first.",
    "needs_access": "{app} is connected but you have not been allowed to use it. A card lets the person allow it for "
                    "you. End your turn now and wait. You cannot grant yourself access.",
    "ready": "{app} is connected and allowed for you. Its tools appear from your next turn. If they are still "
             "missing, end your turn and say so in one sentence.",
}


class ConnectError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _clean(app):
    if not isinstance(app, str):
        raise ConnectError("invalid_input", "Give the app's short name, such as gmail.")
    slug = app.strip().lower()
    if not composio_generic.valid_app(slug) or composio_generic.blocked_app(slug):
        raise ConnectError("unsupported_app", "That app cannot be connected from here.")
    return slug


async def _state(owner_id, db, agent, app, make_client):
    if not await composio_service.configured(db, owner_id):
        return "needs_key"
    try:
        found = await composio_service.accounts(db, owner_id, make_client)
    except UserError as exc:
        raise ConnectError("connection_check_failed", exc.message) from None
    if not any(a.toolkit == app and a.status == "ACTIVE" for a in found):
        return "needs_connection"
    scopes = agent.connector_scopes if isinstance(agent.connector_scopes, dict) else {}
    return "ready" if scopes.get(app) in composio_tools.LEVELS else "needs_access"


async def call(rt, run, args, make_client):
    """The tool's answer as a dict (errors carry `error` and `code`)."""
    try:
        if not isinstance(args, dict) or set(args) - {"app", "reason"}:
            raise ConnectError("invalid_input", "Send only app and reason.")
        app = _clean(args.get("app"))
        reason = args.get("reason", "")
        if not isinstance(reason, str) or len(reason) > REASON_MAX:
            raise ConnectError("invalid_input", f"Give the reason in at most {REASON_MAX} characters.")
        async with rt.session() as db:
            agent = await db.get(Agent, run.agent_id)
            if agent is None or agent.owner_id != rt.owner_id or agent.archived_at is not None:
                raise ConnectError("agent_not_found", "No live agent with that id.")
            state = await _state(rt.owner_id, db, agent, app, make_client)
            agent_name = fence.line(agent.name)
    except ConnectError as exc:
        return {"error": str(exc), "code": exc.code}
    request_id = f"connect-{run.turn_id}-{app}"
    await run.recorder.emit(EVENT, {
        "request_id": request_id, "app": app, "state": state, "reason": fence.line(reason, REASON_MAX),
        "agent_id": run.agent_id, "agent_name": agent_name,
    })
    return {"state": state, "app": app, "request_id": request_id, "next": NEXT[state].format(app=app)}
