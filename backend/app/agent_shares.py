"""The stored share-of-mailbox rule of an agent (`agent.connector_shares`) and its public view.

Stored: {"gmail": {"<composio account id>": {"percent": 1-100, "seed": "<32 hex>", "pins": [thread ids]}}}.
No entry for an account means the whole mailbox. The rule is enforced elsewhere on every Gmail call; this
module only shapes, merges and hides what is stored. The seed picks which threads are in the share, so it
never leaves the server: every view a client can read goes through `public`.
"""
import re
import secrets

SHARE_APPS = ("gmail",)
MAX_PINS = 200
MAX_PIN_LENGTH = 200
_SEED = re.compile(r"^[0-9a-f]{32}$")


def new_seed() -> str:
    return secrets.token_hex(16)


def valid_seed(value: object) -> bool:
    return isinstance(value, str) and _SEED.match(value) is not None


def _entries(value: object):
    """(app, account id, entry) for every well-formed entry of a stored or submitted value."""
    if not isinstance(value, dict):
        return
    for app, accounts in value.items():
        if not isinstance(app, str) or not isinstance(accounts, dict):
            continue
        for account_id, entry in accounts.items():
            if isinstance(account_id, str) and isinstance(entry, dict):
                yield app, account_id, entry


def public(value: object) -> dict:
    """What a client may read: percent and pins per account, never the seed."""
    out: dict = {}
    for app, account_id, entry in _entries(value):
        percent = entry.get("percent")
        if type(percent) is not int:
            continue
        pins = entry.get("pins")
        out.setdefault(app, {})[account_id] = {
            "percent": percent,
            "pins": [pin for pin in pins if isinstance(pin, str)] if isinstance(pins, list) else [],
        }
    return out


def seeds_of(value: object) -> dict[str, dict[str, str]]:
    """The valid seeds in a stored value, {app: {account id: seed}} (a restore keeps the same sample)."""
    out: dict = {}
    for app, account_id, entry in _entries(value):
        if valid_seed(entry.get("seed")):
            out.setdefault(app, {})[account_id] = entry["seed"]
    return out


def merge(submitted: object, current: object, seeds: dict | None = None) -> dict:
    """The stored value for a validated submission.

    A seed is kept from `current` when the account already has an entry, so raising the percent only adds
    threads. A new entry, or `reshuffle`, gets a new seed; a restore supplies the old seed in `seeds`.
    Pins not given keep the current ones."""
    kept = {(app, account_id): entry for app, account_id, entry in _entries(current)}
    out: dict = {}
    for app, account_id, entry in _entries(submitted):
        before = kept.get((app, account_id)) or {}
        supplied = (seeds or {}).get(app, {}).get(account_id)
        if valid_seed(supplied):
            seed = supplied
        elif entry.get("reshuffle") or not valid_seed(before.get("seed")):
            seed = new_seed()
        else:
            seed = before["seed"]
        pins = entry.get("pins")
        if pins is None:
            pins = before.get("pins") if isinstance(before.get("pins"), list) else []
        out.setdefault(app, {})[account_id] = {
            "percent": entry["percent"], "seed": seed, "pins": list(dict.fromkeys(pins)),
        }
    return out


def clean(value: object, app_scopes: object, account_scopes: object) -> dict:
    """The stored value with every entry dropped whose app is no longer allowed or whose account is no
    longer one of the agent's accounts for that app."""
    levels = app_scopes if isinstance(app_scopes, dict) else {}
    accounts = account_scopes if isinstance(account_scopes, dict) else {}
    out: dict = {}
    for app, account_id, entry in _entries(value):
        ids = accounts.get(app)
        if app in levels and isinstance(ids, list) and account_id in ids:
            out.setdefault(app, {})[account_id] = entry
    return out


def check(submitted: object, app_scopes: object, account_scopes: object) -> str | None:
    """None when a submission is allowed, else why not. Only the apps in SHARE_APPS, only accounts the agent
    may use for an app that is on, percent 1-100, bounded pins."""
    levels = app_scopes if isinstance(app_scopes, dict) else {}
    accounts = account_scopes if isinstance(account_scopes, dict) else {}
    if not isinstance(submitted, dict):
        return "A share is a map of apps to accounts."
    for app, by_account in submitted.items():
        if app not in SHARE_APPS:
            return "A share can only be set for Gmail."
        if app not in levels:
            return "Allow the app first; a share narrows an app the agent may use."
        if not isinstance(by_account, dict):
            return "A share is a map of apps to accounts."
        for account_id, entry in by_account.items():
            ids = accounts.get(app)
            if not isinstance(ids, list) or account_id not in ids:
                return "A share can only be set for an account this agent may use."
            if not isinstance(entry, dict) or type(entry.get("percent")) is not int or not 1 <= entry["percent"] <= 100:
                return "A share is a whole percent from 1 to 100."
            pins = entry.get("pins")
            if pins is not None and (not isinstance(pins, list) or len(pins) > MAX_PINS
                                     or any(not isinstance(p, str) or not p or len(p) > MAX_PIN_LENGTH for p in pins)):
                return f"Pin at most {MAX_PINS} threads."
    return None
