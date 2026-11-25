"""`request_app_connection`: any agent asks the person to connect an app from the chat.

An agent that needs an app it has no tools for (Gmail, GitHub, a calendar...) used to ask the
owner where to find the data and stop. Now it calls this tool, which checks the three things only
the person can supply and records one card in the conversation:

  needs_key         connected apps are not set up on this computer yet (no Composio key)
  needs_connection  no active account of the app exists
  needs_access      an account exists, but this agent may not use the app yet
  ready             the account exists and the agent may use it (tools mount on its next turn)

The tool never connects, grants or stores anything and never takes a credential: the card sends
the person to the app's own sign-in, and access stays the person's decision in the agent's
settings. The result tells the agent to end its turn; the person's reply resumes it.
"""
from app import composio_generic, composio_tools
from app.db.models import Agent
from app.services import composio_service
from app.services.composio_service import UserError
from app.team import fence, service

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


def _clean(app):
    if not isinstance(app, str):
        raise service.TeamError("invalid_input", "Give the app's short name, such as gmail.")
    slug = app.strip().lower()
    if not composio_generic.valid_app(slug) or composio_generic.blocked_app(slug):
        raise service.TeamError("unsupported_app", "That app cannot be connected from here.")
    return slug


async def _state(rt, db, agent, app):
    if not await composio_service.configured(db, rt.owner_id):
        return "needs_key"
    from app.services import composio_bridge  # looked up late so a test can replace make_client
    try:
        found = await composio_service.accounts(db, rt.owner_id, composio_bridge.make_client)
    except UserError as exc:
        raise service.TeamError("connection_check_failed", exc.message) from None
    if not any(a.toolkit == app and a.status == "ACTIVE" for a in found):
        return "needs_connection"
    scopes = agent.connector_scopes if isinstance(agent.connector_scopes, dict) else {}
    return "ready" if scopes.get(app) in composio_tools.LEVELS else "needs_access"


async def call(rt, run, name, args):
    if name != "request_app_connection":
        raise service.TeamError("unknown_tool", "Unknown team tool.")
    if not isinstance(args, dict) or set(args) - {"app", "reason"}:
        raise service.TeamError("invalid_input", "Send only app and reason.")
    app = _clean(args.get("app"))
    reason = args.get("reason", "")
    if not isinstance(reason, str) or len(reason) > REASON_MAX:
        raise service.TeamError("invalid_input", f"Give the reason in at most {REASON_MAX} characters.")
    async with rt.session() as db:
        agent = await service.member(db, rt.owner_id, run.agent_id, team_id=run.ctx.team.team_id)
        state = await _state(rt, db, agent, app)
        agent_name = fence.line(agent.name)
    request_id = f"connect-{run.turn_id}-{app}"
    await run.recorder.emit(EVENT, {
        "request_id": request_id, "app": app, "state": state, "reason": fence.line(reason, REASON_MAX),
        "agent_id": run.agent_id, "agent_name": agent_name,
    })
    return {"state": state, "app": app, "request_id": request_id, "next": NEXT[state].format(app=app)}
