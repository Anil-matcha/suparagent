"""Provider-specific rules that keep an agent to the mailboxes and numbers assigned to it.

Assignment alone only decides whether a provider's tools are mounted (agent_service_store). These rules
look inside a call. They are written against the tool definitions the services reported on live accounts:

* AgentMail (2026-11-16): every inbox-scoped tool has a required `inboxId`, and the inbox id is the email address.
* AgentPhone (2026-11-17): numbers are named by `number_id`, `from_number_id` or `from_number` (E.164), or only
  implicitly through an AgentPhone `agent_id`; a call or text with no explicit sender would go out from whatever
  number that agent has attached, so a sender must be named and must be assigned.

A provider without a rule here is not narrowed by argument; the owner still sees every argument on the approval
card.
"""

import json

# Providers whose tools name the inbox in an `inboxId` argument.
_INBOX_ID_PROVIDERS = {"agentmail"}
_LIST_TOOLS = {"list_inboxes"}

# AgentPhone tools an agent never gets: they spend money, set up inbound delivery (which stays off), or delete an
# agent on the account. Everything else the owner grants still needs a card for every call.
_BLOCKED = {"agentphone": {"buy_number", "set_webhook", "delete_webhook", "delete_agent"}}
# The arguments that name the sending number. At least one must be present and every one present must be assigned.
_PHONE_SENDERS = {
    "send_message": ("number_id", "from_number"),
    "make_call": ("from_number_id",),
    "make_conversation_call": ("from_number_id",),
}
# Reads that are about one number: its id is required (list_calls could otherwise list every number's calls).
_PHONE_NUMBER_ID = {"check_readiness": "number_id", "get_messages": "number_id", "list_calls": "number_id"}
_PHONE_NUMBER_IDS = {"watch_messages": "number_ids"}

NOT_ASSIGNED = "resource_not_assigned"
NOT_ALLOWED = "tool_not_allowed"


def blocked(provider: str, tool_name: str) -> bool:
    """A tool the agent is never given for this provider (not mounted, refused if called, not grantable)."""
    return tool_name in _BLOCKED.get(provider, ())


def _has_inbox_id(input_schema: dict) -> bool:
    properties = (input_schema or {}).get("properties")
    return isinstance(properties, dict) and "inboxId" in properties


def _phone_refusal(tool_name: str, arguments: dict, assigned: list[str], identifiers: list[str]) -> str | None:
    numbers = {address.replace(" ", "") for address in assigned}
    ids = set(identifiers)

    def listed(field: str, value: object) -> bool:
        if not isinstance(value, str):
            return False
        return value.replace(" ", "") in numbers if field == "from_number" else value in ids

    if tool_name in _PHONE_SENDERS:
        fields = [field for field in _PHONE_SENDERS[tool_name] if arguments.get(field) is not None]
        if not fields or not all(listed(field, arguments[field]) for field in fields):
            return NOT_ASSIGNED
    field = _PHONE_NUMBER_ID.get(tool_name)
    if field is not None and not listed(field, arguments.get(field)):
        return NOT_ASSIGNED
    field = _PHONE_NUMBER_IDS.get(tool_name)
    if field is not None:
        values = arguments.get(field)
        if not isinstance(values, list) or not values or not all(listed("number_id", value) for value in values):
            return NOT_ASSIGNED
    return None


def refusal(provider: str, input_schema: dict, arguments: dict, assigned: list[str], *,
            tool_name: str = "", identifiers: list[str] | tuple = ()) -> str | None:
    """A reason code when the call is not allowed for this agent, else None.

    `resource_not_assigned`: it names (or, for a sender, fails to name) a mailbox or number the agent was not given.
    `tool_not_allowed`: the tool is one agents never get for this provider."""
    args = arguments if isinstance(arguments, dict) else {}
    if blocked(provider, tool_name):
        return NOT_ALLOWED
    if provider == "agentphone":
        return _phone_refusal(tool_name, args, list(assigned), list(identifiers))
    if provider not in _INBOX_ID_PROVIDERS or not _has_inbox_id(input_schema):
        return None
    value = args.get("inboxId")
    if not isinstance(value, str) or value.strip().lower() not in {address.lower() for address in assigned}:
        return NOT_ASSIGNED
    return None


def narrows_result(provider: str, tool_name: str) -> bool:
    return (provider in _INBOX_ID_PROVIDERS and tool_name in _LIST_TOOLS) or (
        provider == "agentphone" and tool_name == "list_numbers")


def _readable(result: dict, key: str) -> dict | None:
    data = result.get("structuredContent")
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return data
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                parsed = json.loads(block.get("text", ""))
            except ValueError:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get(key), list):
                return parsed
    return None


def narrow_result(result: dict, assigned: list[str], *, provider: str = "agentmail",
                  identifiers: list[str] | tuple = ()) -> dict:
    """The tool result with only the assigned inboxes or numbers left in it. Fails closed: a result that cannot be
    read as that list is replaced by an error rather than passed through."""
    if result.get("isError") or result.get("error"):
        return result
    phone = provider == "agentphone"
    data = _readable(result, "data" if phone else "inboxes")
    if data is None:
        what = "number" if phone else "inbox"
        return {"error": f"The {what} list could not be limited to this agent's {what}s, so it was withheld.",
                "code": "result_not_filterable"}
    if phone:
        numbers, ids = {a.replace(" ", "") for a in assigned}, set(identifiers)
        rows = [row for row in data["data"] if isinstance(row, dict)
                and (row.get("id") in ids or str(row.get("phoneNumber", "")).replace(" ", "") in numbers)]
        kept = {**data, "data": rows, "total": len(rows), "hasMore": False}
    else:
        allowed = {address.lower() for address in assigned}
        rows = [row for row in data["inboxes"] if isinstance(row, dict) and str(row.get("inboxId", "")).lower() in allowed]
        kept = {**data, "inboxes": rows, "count": len(rows)}
    return {"content": [{"type": "text", "text": json.dumps(kept)}], "structuredContent": kept, "isError": False}


# What a read-only look-up asks the service for, and where the answer is. Only providers whose tools were seen live.
_LOOKUP = {"agentmail": ("list_inboxes", "inboxes"), "agentphone": ("list_numbers", "data")}


def lookup_tool(provider: str) -> str | None:
    found = _LOOKUP.get(provider)
    return found[0] if found else None


def _text(value: object, limit: int) -> str:
    """One line of plain text from a service's answer: control characters dropped, length capped."""
    return " ".join("".join(ch for ch in str(value) if ch.isprintable()).split())[:limit]


def candidates(provider: str, result: dict) -> list[dict] | None:
    """The mailboxes or numbers the service reports, as {address, provider_resource_id, label}; None when the
    answer cannot be read as that list. The service's text is untrusted: it is cut to plain short strings."""
    found = _LOOKUP.get(provider)
    if found is None or result.get("isError") or result.get("error"):
        return None
    data = _readable(result, found[1])
    if data is None:
        return None
    rows = []
    for row in data[found[1]]:
        if not isinstance(row, dict):
            continue
        if provider == "agentmail":
            identifier = row.get("inboxId")
            address, label = row.get("email") or identifier, row.get("displayName") or ""
        else:
            identifier, address, label = row.get("id"), row.get("phoneNumber"), ""
        if isinstance(identifier, str) and isinstance(address, str) and address:
            rows.append({"address": _text(address, 200), "provider_resource_id": _text(identifier, 200),
                         "label": _text(label, 100)})
    return rows
