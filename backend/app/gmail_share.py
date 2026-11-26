"""The share of a mailbox an agent may see (BL-172).

An owner can let an agent work on a fixed share of the threads in a connected Gmail account,
for example 5%, and raise it later. The rule is kept per agent and per Composio account in
`agent.connector_shares` and enforced here, on every Gmail call (services/composio_service.run_tool),
so the agent only ever receives threads inside its share. It is never an instruction the agent
could ignore.

  {"gmail": {"<account id>": {"percent": 1-100, "seed": "<32 hex>", "pins": ["<thread id>", ...]}}}

A thread is in the share when its id is pinned, or when a keyed hash of its id falls under the
percentage. The hash is keyed by a per-account seed, so it is stable (the same thread always gets
the same answer), monotone (raising the percentage only adds threads, never hides one already
seen), independent between agents and accounts, and not guessable by a sender. The whole thread is
in or out together, because the key is the Gmail thread id.

No entry for an account, or percent 100, means the whole mailbox, exactly as before. Anything
malformed fails closed: reads are denied, never widened.

This module is pure: no I/O, no database. The call sites are in composio_service.run_tool.
"""
import hashlib
import hmac
from dataclasses import dataclass

from app import composio_tools

APP = "gmail"
BUCKETS = 10_000          # a percent is 100 buckets, so 0.01% resolution is available later
MAX_PINS = 200
MAX_ID = 200

OUTSIDE = {"error": "That thread is outside the share of mail this agent may use.", "code": "outside_share"}
INVALID = {"error": "This agent's share of mail is not set up correctly, so its mail is not shown. "
                    "The person can fix it in the agent's settings.", "code": "share_invalid"}
NO_SEND = {"error": "Sending email is switched off while this agent sees only a share of the mailbox. "
                    "Save a draft reply instead; the person sends it.", "code": "share_blocks_send"}

# The tools that name a thread, so they can be refused before anything is sent to Gmail.
THREAD_TOOLS = frozenset({"gmail_fetch_thread", "gmail_create_draft_reply", "gmail_prepare_checked_reply"})


class ShareInvalid(ValueError):
    """The stored rule cannot be read; the caller must deny, not grant."""


@dataclass(frozen=True)
class ShareRule:
    percent: int
    seed: bytes
    pins: frozenset


def rule_from_json(value: object) -> ShareRule | None:
    """One account's stored entry as a rule, None for "whole mailbox", or ShareInvalid."""
    if value is None or value == {}:
        return None
    if not isinstance(value, dict):
        raise ShareInvalid("entry is not an object")
    percent, seed, pins = value.get("percent"), value.get("seed"), value.get("pins", [])
    if type(percent) is not int or not 1 <= percent <= 100:
        raise ShareInvalid("percent")
    if not isinstance(seed, str) or len(seed) != 32:
        raise ShareInvalid("seed")
    try:
        key = bytes.fromhex(seed)
    except ValueError:
        raise ShareInvalid("seed") from None
    if (not isinstance(pins, list) or len(pins) > MAX_PINS
            or any(not isinstance(pin, str) or not pin or len(pin) > MAX_ID for pin in pins)):
        raise ShareInvalid("pins")
    if percent == 100:
        return None
    return ShareRule(percent, key, frozenset(pins))


def rule_for(shares: object, app: str, account_id: str) -> ShareRule | None:
    """The rule for one account from an agent's whole `connector_shares`, or None.

    Only the entry for this app and account is read, so a damaged entry for another account never
    blocks this one; a damaged entry for this account raises ShareInvalid."""
    if not shares:
        return None
    if not isinstance(shares, dict):
        raise ShareInvalid("not an object")
    per_app = shares.get(app)
    if per_app is None:
        return None
    if not isinstance(per_app, dict):
        raise ShareInvalid("app entry is not an object")
    return rule_from_json(per_app.get(account_id))


def bucket(seed: bytes, thread_id: str) -> int:
    digest = hmac.new(seed, thread_id.encode("utf-8"), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big") % BUCKETS


def in_share(rule: ShareRule | None, thread_id: object) -> bool:
    """Whether a thread may be shown. A missing or odd thread id is out (fail closed)."""
    if rule is None:
        return True
    if not isinstance(thread_id, str) or not thread_id or len(thread_id) > MAX_ID:
        return False
    return thread_id in rule.pins or bucket(rule.seed, thread_id) < rule.percent * (BUCKETS // 100)


def precheck(tool_name: str, arguments: dict, rule: ShareRule | None) -> dict | None:
    """An error to return before anything is sent to Gmail, or None to go on."""
    if rule is None:
        return None
    if tool_name == "gmail_send_email":
        return dict(NO_SEND)  # a new email names no thread, so it cannot be checked against the share
    if tool_name in THREAD_TOOLS and not in_share(rule, arguments.get("thread_id")):
        return dict(OUTSIDE)
    return None


def thread_of(row: object) -> object:
    return (row.get("threadId") or row.get("thread_id")) if isinstance(row, dict) else None


def split_rows(tool: composio_tools.Tool, data: object, rule: ShareRule | None) -> tuple[list, int, str | None]:
    """(rows in the share, how many rows the page had, the page token) from a raw search answer.

    Run on Composio's raw answer, before composio_tools.project, so that `count` and `truncated`
    describe only what is shown and nothing reveals how much was hidden."""
    rows = composio_tools._rows(tool, data)
    if not isinstance(rows, list):
        raise composio_tools.BadArguments("The connected app answered in a form this app does not read.")
    kept = [row for row in rows if isinstance(row, dict) and in_share(rule, thread_of(row))]
    cursor = composio_tools._find(data, lambda value: isinstance(value, dict) and isinstance(value.get(tool.cursor_key), str))
    token = cursor[tool.cursor_key] if isinstance(cursor, dict) and cursor[tool.cursor_key] else None
    return kept, len(rows), token


def message_in_share(result: dict, rule: ShareRule | None) -> bool:
    """Whether a projected single-message answer belongs to a thread in the share."""
    shown = result.get("result")
    return rule is None or (isinstance(shown, dict) and in_share(rule, shown.get("threadId")))


def strip_profile(result: dict, rule: ShareRule | None) -> dict:
    """The profile without the mailbox totals, which would say how much mail there is."""
    shown = result.get("result")
    if rule is not None and isinstance(shown, dict):
        for field in ("messagesTotal", "threadsTotal"):
            shown.pop(field, None)
    return result
