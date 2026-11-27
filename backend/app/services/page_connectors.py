"""Connected-app calls made by a page (inline in a thread, or a saved app), without an engine turn.

A page may call only the tools it declared in `uses` ("toolkit:TOOL", services/pages_bridge.py), and the server
re-derives that list from the stored page every time. The call then runs through the same path a turn's call takes:
the person's own Composio connection, the accounts and level the page's agent was granted (read again from the
database on every call), the agent's share-of-mailbox rules, the approval policy (composio_policy.automatic, with the
agent's approval mode) and the same result bounds. Nothing here gives a page the app database: `data` is not served.

A call that may change something needs (1) `engaged`, the host's own click-detected flag, and (2) whatever the
approval policy asks: a tool the policy decides on a card returns `needs_approval` with a one-use `confirmation`
token; the host shows the summary, and the person's answer is the same call sent again with that token.

Limits: arguments 64 KB, result 512 KB, 60 s, error text 1000 characters, 30 calls a minute per page and 60 a
minute per agent and toolkit (in memory, per process).
"""
import asyncio
import hashlib
import json
import secrets
import time
from collections import defaultdict, deque

from app import composio_generic, composio_tools
from app.core.logging import get_logger
from app.services import composio_actions, composio_bridge, composio_policy, composio_service, decision_log
from app.services.composio_client import ComposioError
from app.services.pages_bridge import declared_connectors

logger = get_logger(__name__)

ARGS_MAX_BYTES = 64 * 1024
RESULT_MAX_BYTES = 512 * 1024
TIMEOUT_SECONDS = 60
ERROR_MAX = 1000
PAGE_LIMIT, AGENT_TOOLKIT_LIMIT, WINDOW_SECONDS = 30, 60, 60.0
CONFIRM_TTL_SECONDS = 120
CONFIRM_MAX = 200
# Tools that record in this app's own database or files rather than at the app: never reachable from a page.
_NOT_FOR_PAGES = {"triage_mark", "lesson_propose", "sent_replies", "label_apply", "label_list"}


class RpcError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


_windows: dict[tuple, deque] = defaultdict(deque)
_confirmations: dict[str, tuple[str, float]] = {}


def reset() -> None:
    """For tests."""
    _windows.clear()
    _confirmations.clear()


def _take(key: tuple, limit: int, now: float) -> bool:
    window = _windows[key]
    while window and now - window[0] >= WINDOW_SECONDS:
        window.popleft()
    if len(window) >= limit:
        return False
    window.append(now)
    return True


def _rate_limit(page_key: tuple, agent_id: int, toolkit: str) -> None:
    now = time.monotonic()
    agent_key = ("agent", agent_id, toolkit)
    # Check both before recording either, so a refused call does not use up the other window.
    for key, limit in ((page_key, PAGE_LIMIT), (agent_key, AGENT_TOOLKIT_LIMIT)):
        window = _windows[key]
        while window and now - window[0] >= WINDOW_SECONDS:
            window.popleft()
        if len(window) >= limit:
            raise RpcError(429, "rate_limited", "This page is making too many calls. Wait a moment and try again.")
    _take(page_key, PAGE_LIMIT, now)
    _take(agent_key, AGENT_TOOLKIT_LIMIT, now)


REDACT_CHUNK = 1000


def _redact_text(text: str) -> str:
    """decision_log.redact on bounded pieces: its named-assignment pattern is quadratic on a long unbroken run of
    letters, so a big string is cut at spaces into pieces of about REDACT_CHUNK characters first."""
    if len(text) <= REDACT_CHUNK:
        return decision_log.redact(text)
    out, start = [], 0
    while start < len(text):
        end = min(start + REDACT_CHUNK, len(text))
        if end < len(text):
            space = max(text.rfind(" ", start, end), text.rfind("\n", start, end))
            end = space + 1 if space > start else end
        out.append(decision_log.redact(text[start:end]))
        start = end
    return "".join(out)


def _limit_error(text: object) -> str:
    return _redact_text(str(text)[:ERROR_MAX * 4])[:ERROR_MAX]


def _redacted(value):
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    return value


def _resolve(toolkit: str, tool: str):
    """("curated", Tool) or ("generic", slug) for a declared tool, or an RpcError that says why it is not available."""
    if composio_generic.blocked_app(toolkit) or composio_generic.blocked_action(tool):
        raise RpcError(409, "not_available", "That connected-app tool cannot be used from a page.")
    if toolkit in composio_tools.apps():
        found = next((t for t in composio_tools.TOOLS if t.app == toolkit and t.slug == tool), None)
        if found is None or found.special in _NOT_FOR_PAGES:
            raise RpcError(409, "not_available", f"{tool} is not a tool a page can use. Only the tools Suparagent supports for {toolkit} are available.")
        return "curated", found
    return "generic", tool


def _mutating(kind: str, target) -> bool:
    if kind == "curated":
        return target.kind != "read"
    return target not in composio_generic.REVIEWED_READS  # no generic action is trusted as a read until reviewed


def _label(toolkit: str) -> str:
    return toolkit.replace("-", " ").replace("_", " ").title()


async def agent_grants(db, owner_id: str, agent_id: int | None) -> dict | None:
    """{agent, levels, accounts, shares} as the agent stands now, or None for no agent (or one that is gone or archived)."""
    from app.db.models import Agent

    agent = await db.get(Agent, agent_id) if agent_id is not None else None
    if agent is None or agent.archived_at is not None or agent.owner_id != owner_id:
        return None
    return {"agent": agent, "levels": composio_bridge.apps_for(agent.connector_scopes),
            "accounts": composio_bridge.accounts_for(agent.connector_account_scopes),
            "shares": agent.connector_shares or {}}


async def list_connectors(db, owner_id: str, grants: dict | None, uses: object) -> dict:
    """Only the declared toolkits, with whether the person has an account connected there and whether the page's
    agent may use it. A missing key or an unreachable Composio reads as not connected."""
    declared = declared_connectors(uses)
    active: set[str] = set()
    if declared:
        record = await composio_service.load(db, owner_id)
        if record is not None:
            try:
                found = await composio_bridge.make_client(record["api_key"]).accounts(record["user_id"])
                active = {a.toolkit for a in found if a.status == "ACTIVE"}
            except ComposioError:
                active = set()
    out = []
    for group in declared:
        toolkit, tools = group["toolkit"], []
        for tool in group["tools"]:
            try:
                tools.append({"tool": tool, "mutating": _mutating(*_resolve(toolkit, tool))})
            except RpcError:
                tools.append({"tool": tool, "mutating": True, "available": False})
        allowed = bool(grants and toolkit in grants["levels"] and grants["accounts"].get(toolkit))
        out.append({"toolkit": toolkit, "label": _label(toolkit), "connected": toolkit in active,
                    "allowed": allowed, "tools": tools})
    return {"connectors": out}


def _call_key(owner_id: str, page_key: tuple, toolkit: str, tool: str, arguments: dict) -> str:
    body = json.dumps([owner_id, list(page_key), toolkit, tool, arguments], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def _issue_confirmation(key: str) -> str:
    now = time.monotonic()
    for token in [t for t, (_, until) in _confirmations.items() if until <= now]:
        del _confirmations[token]
    while len(_confirmations) >= CONFIRM_MAX:
        _confirmations.pop(next(iter(_confirmations)))
    token = secrets.token_urlsafe(24)
    _confirmations[token] = (key, now + CONFIRM_TTL_SECONDS)
    return token


def _consume_confirmation(token: object, key: str) -> bool:
    entry = _confirmations.pop(token, None) if isinstance(token, str) else None
    return entry is not None and entry[0] == key and entry[1] > time.monotonic()


def _summary(toolkit: str, tool: str, arguments: dict) -> str:
    text = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    return _redact_text(f"Run {tool} on {_label(toolkit)} with {text[:2000]}")[:300]


def _refusal_message(reason: str, toolkit: str) -> str:
    return {
        "plan_mode": "This agent is in plan mode, which changes nothing, so this call was not made.",
        "write_not_allowed": f"This agent may only read {_label(toolkit)}; it has not been allowed to change anything there.",
        "app_not_allowed": f"This agent has not been given access to {_label(toolkit)}.",
        "no_connected_apps": "This agent has no connected apps.",
    }.get(reason, "This agent is not allowed to make that call.")


def _failure(result: dict) -> RpcError:
    code = str(result.get("code") or "error")
    text = _limit_error(result.get("error") or "The call could not be completed.")
    if code in ("not_connected", "not_allowed", "unknown_account", "account_required", "not_configured", "bad_arguments",
                "unsupported_schema", "deprecated_action", "wrong_app", "blocked_app", "blocked_action", "use_curated",
                "generic_not_available", "share_invalid", "outside_share", "not_in_share"):
        return RpcError(409 if code != "bad_arguments" else 422, code, text)
    if code == "outcome_unknown":
        return RpcError(502, code, text)
    return RpcError(502, code, text)


async def call(db, *, owner_id: str, page_key: tuple, thread_id: int | None, uses: object, grants: dict | None,
               toolkit: str, tool: str, arguments: object, account: object, engaged: bool, confirmation: object) -> dict:
    """One page call. Raises RpcError for every refusal; returns {"result": ..., "truncated"?: True}."""
    if not any(g["toolkit"] == toolkit and tool in g["tools"] for g in declared_connectors(uses)):
        raise RpcError(409, "not_declared", f'This page did not declare {toolkit}:{tool} in its "uses", so it cannot call it. '
                                           f'Ask the agent to update the page with "{toolkit}:{tool}" in uses.')
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise RpcError(422, "bad_arguments", "arguments must be an object.")
    if len(json.dumps(arguments, ensure_ascii=False, default=str).encode("utf-8")) > ARGS_MAX_BYTES:
        raise RpcError(413, "arguments_too_large", "The arguments are too large (at most 64 KB).")
    try:
        alias = composio_generic._clean_account(account)
    except composio_generic.GenericError as exc:
        raise RpcError(422, "bad_arguments", str(exc)) from None
    kind, target = _resolve(toolkit, tool)
    if grants is None:
        raise RpcError(409, "no_agent", "This page has no agent that can use connected apps.")
    levels, account_ids = grants["levels"], grants["accounts"].get(toolkit, [])
    if toolkit not in levels or not account_ids:
        raise RpcError(409, "not_granted", f"This page's agent has not been given access to {_label(toolkit)}. "
                                           "Allow it on the agent's Apps settings, then try again.")
    agent, mode = grants["agent"], grants["agent"].approval_mode
    mutating = _mutating(kind, target)
    if kind == "curated":
        policy_args = None
        name = target.name
    else:
        policy_args = {"app": toolkit, "action": tool}
        name = composio_generic.RUN
    decision = composio_policy.automatic(composio_policy.PREFIX + name, mode, levels, policy_args)
    audit = dict(thread_id=thread_id, tool=composio_policy.PREFIX + name, kind="write" if mutating else "read",
                 mode=mode, summary=_summary(toolkit, tool, arguments))
    if decision is not None and decision[0] == "denied":
        decision_log.record("denied", source="page_" + decision[1], **audit)
        raise RpcError(409, "not_allowed", _refusal_message(decision[1], toolkit))
    key = _call_key(owner_id, page_key, toolkit, tool, arguments)
    source = "page_read"
    if mutating:
        if engaged is not True:
            raise RpcError(409, "needs_engagement", "This changes something, so it only runs after the person clicks in the page.")
        source = "page_change"
        if decision is None:
            # The approval level would ask: never run silently. The host shows the summary, then sends the call again.
            if not _consume_confirmation(confirmation, key):
                decision_log.record("shown", source="page_approval", **audit)
                raise RpcError(409, "needs_approval", "The person needs to approve this before it runs.",
                               summary=_summary(toolkit, tool, arguments), confirmation=_issue_confirmation(key), toolkit=toolkit, tool=tool)
            source = "page_confirmed"
    _rate_limit(page_key, agent.id, toolkit)
    try:
        result = await asyncio.wait_for(_run(db, owner_id, grants, kind, target, toolkit, tool, arguments, alias, account_ids),
                                        TIMEOUT_SECONDS)
    except TimeoutError:
        decision_log.record("allowed", source=source + "_timeout", **audit)
        raise RpcError(504, "timeout", "The app took too long to answer" + (". The change may or may not have been made; check the app before repeating it." if mutating else ".")) from None
    except RpcError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("a page's connected-app call failed")
        raise RpcError(502, "composio_error", "The call could not be completed.") from None
    decision_log.record("allowed" if source != "page_read" else "auto_allowed", source=source, **audit)
    if isinstance(result, dict) and result.get("error"):
        raise _failure(result)
    return _bounded(result)


def _bounded(result: object) -> dict:
    cut = bool(isinstance(result, dict) and result.get("truncated"))
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text.encode("utf-8")) > RESULT_MAX_BYTES:
        result, cut = {"text": text.encode("utf-8")[:RESULT_MAX_BYTES].decode("utf-8", "ignore")}, True
    result = _redacted(result)
    out = {"result": result}
    if cut:
        out["truncated"] = True
    return out


async def _run(db, owner_id, grants, kind, target, toolkit, tool, arguments, alias, account_ids) -> dict:
    agent = grants["agent"]
    factory = lambda key: composio_bridge.make_client(key)  # noqa: E731  (looked up when used, so a test can replace make_client)
    if kind == "curated":
        try:
            sent, chosen = composio_tools.clean_arguments(target, {**arguments, **({"account": alias} if alias else {})})
        except composio_tools.BadArguments as exc:
            return {"error": str(exc), "code": "bad_arguments"}
        return await composio_service.run_tool(
            db, owner_id, factory, target, sent, chosen, allowed_account_ids=account_ids, shares=grants["shares"],
            mark_context={"agent_id": agent.id, "turn_id": None, "run_id": None})
    refused = composio_generic.refusal_for(toolkit, tool)
    if refused:
        return {"error": refused[1], "code": refused[0]}
    record = await composio_service.load(db, owner_id)
    if record is None:
        return {"error": "Connected apps are not set up. The person can add their Composio key on the Apps page.", "code": "not_configured"}
    try:
        info = await factory(record["api_key"]).tool(tool)
    except ComposioError as exc:
        return composio_service._agent_error(exc)
    if info.toolkit != toolkit:
        return {"error": f"That action does not belong to {toolkit}.", "code": "wrong_app"}
    request = {"app": toolkit, "action": tool, "revision": composio_generic.revision_of(info.slug, info.version, info.schema),
               "arguments": arguments, "account": alias}
    return await composio_actions.run(db, owner_id, factory, agent_id=agent.id, auto_called=False, request=request,
                                      allowed_account_ids=account_ids)
