"""Named Claude accounts (BL-108).

Each account is its own `CLAUDE_CONFIG_DIR` under the app data folder, so two
accounts never share a token or a session. An agent with no account keeps the
CLI's existing default folder, exactly as before. The registry stores only an
id and a label; credentials stay inside the account's folder, which is mode
0700 and is never listed, logged or returned.
"""

import json
import os
import re
import secrets
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.core import tenant_paths

MAX_ACCOUNTS = 10
LABEL_MAX = 60
_ID = re.compile(r"acct-[0-9a-f]{12}")
_lock = threading.Lock()


class AccountError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def base_dir() -> Path:
    mine = tenant_paths.root()
    return (mine if mine is not None else Path.home() / ".open-dots" / "local") / "claude-accounts"


def _registry() -> Path:
    return base_dir() / "accounts.json"


def _ensure_base() -> Path:
    base = base_dir()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(base, 0o700)
    return base


def clean_label(label) -> str:
    if not isinstance(label, str):
        raise AccountError("invalid_label", "Give the account a name.", 422)
    value = " ".join(label.split())
    if not value or len(value) > LABEL_MAX or any(ord(c) < 32 for c in value):
        raise AccountError("invalid_label", f"The name must be 1 to {LABEL_MAX} characters on one line.", 422)
    return value


def _read() -> list[dict]:
    try:
        data = json.loads(_registry().read_text("utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("accounts") if isinstance(data, dict) else None
    return [a for a in (items or []) if isinstance(a, dict) and _ID.fullmatch(str(a.get("id", ""))) and isinstance(a.get("label"), str)]


def _write(items: list[dict]) -> None:
    base = _ensure_base()
    tmp = base / f".accounts-{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"accounts": items}, handle)
    os.replace(tmp, _registry())


def list_accounts() -> list[dict]:
    """Id, label and creation time. Never a path or any credential."""
    return [{"id": a["id"], "label": a["label"], "created_at": a.get("created_at")} for a in _read()]


def get(account_id: str) -> dict:
    if not isinstance(account_id, str) or not _ID.fullmatch(account_id):
        raise AccountError("account_not_found", "That Claude account does not exist.", 404)
    for item in _read():
        if item["id"] == account_id:
            return item
    raise AccountError("account_not_found", "That Claude account does not exist.", 404)


def exists(account_id: str) -> bool:
    try:
        get(account_id)
        return True
    except AccountError:
        return False


def config_dir(account_id: str | None) -> Path | None:
    """The account's absolute config folder (created 0700), or None for the default account.
    A named account that was removed raises: a turn must never fall back to another login."""
    if account_id is None:
        return None
    get(account_id)
    folder = (base_dir() / account_id).resolve()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    return folder


def create(label) -> dict:
    value = clean_label(label)
    with _lock:
        items = _read()
        if len(items) >= MAX_ACCOUNTS:
            raise AccountError("too_many_accounts", f"At most {MAX_ACCOUNTS} Claude accounts can be added.")
        if any(a["label"].casefold() == value.casefold() for a in items):
            raise AccountError("label_taken", "Another Claude account already has that name.")
        item = {"id": f"acct-{secrets.token_hex(6)}", "label": value, "created_at": datetime.now(UTC).isoformat()}
        _write([*items, item])
    config_dir(item["id"])
    return {"id": item["id"], "label": value, "created_at": item["created_at"]}


def rename(account_id: str, label) -> dict:
    value = clean_label(label)
    with _lock:
        items = _read()
        target = next((a for a in items if a["id"] == account_id), None)
        if target is None:
            raise AccountError("account_not_found", "That Claude account does not exist.", 404)
        if any(a["id"] != account_id and a["label"].casefold() == value.casefold() for a in items):
            raise AccountError("label_taken", "Another Claude account already has that name.")
        target["label"] = value
        _write(items)
    return {"id": target["id"], "label": value, "created_at": target.get("created_at")}


def remove(account_id: str) -> None:
    """Forget the account and delete its folder (its sign-in goes with it). The caller checks no agent uses it."""
    with _lock:
        items = _read()
        if not any(a["id"] == account_id for a in items):
            raise AccountError("account_not_found", "That Claude account does not exist.", 404)
        _write([a for a in items if a["id"] != account_id])
    folder = base_dir() / account_id
    if _ID.fullmatch(account_id) and folder.is_dir() and not folder.is_symlink():
        shutil.rmtree(folder, ignore_errors=True)
