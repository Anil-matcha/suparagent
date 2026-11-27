"""Named Claude accounts (BL-108): list, add, rename, remove, and start a sign-in for one.

Owner only. Only ids and labels are returned, never a folder or a credential.
Sign-in progress, the pasted code and cancel use the existing
`/engines/claude/login/{flow_id}` routes with the flow id returned here.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.db.models import Agent
from app.routers.engines import _error, _login_owner, get_runtime
from app.services import claude_accounts as accounts
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(
    prefix="/engines/claude/accounts",
    tags=["Engines"],
    dependencies=[Depends(require_local_access)],
)


class AccountBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str


def _raise(exc: accounts.AccountError):
    raise _error(exc.status, exc.code, exc.message) from None


@router.get("")
async def list_accounts(request: Request, response: Response):
    _login_owner(request)
    response.headers["Cache-Control"] = "no-store"
    return accounts.list_accounts()


@router.post("", status_code=201)
async def create_account(payload: AccountBody, request: Request):
    _login_owner(request)
    try:
        return accounts.create(payload.label)
    except accounts.AccountError as exc:
        _raise(exc)


@router.patch("/{account_id}")
async def rename_account(account_id: str, payload: AccountBody, request: Request):
    _login_owner(request)
    try:
        return accounts.rename(account_id, payload.label)
    except accounts.AccountError as exc:
        _raise(exc)


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: str, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _login_owner(request)
    try:
        accounts.get(account_id)
        async with rt.session() as db:
            used = await db.scalar(select(func.count()).select_from(Agent).where(Agent.claude_account == account_id))
        if used:
            raise _error(409, "account_in_use", f"{used} agent(s) use this account. Switch them to another account first.")
        if rt._runs or rt._starting_turns:
            raise _error(409, "engine_busy", "Stop running conversations before removing an account.")
        accounts.remove(account_id)
    except accounts.AccountError as exc:
        _raise(exc)


@router.post("/{account_id}/login", status_code=202)
async def start_account_login(account_id: str, request: Request, response: Response, rt: ThreadRuntime = Depends(get_runtime)):
    from app.services.codex_login import LoginError
    from app.services.local_claude_runner import resolve_claude_executable

    owner = _login_owner(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        folder = accounts.config_dir(account_id)
    except accounts.AccountError as exc:
        _raise(exc)
    if rt._closing or "claude" not in rt.adapters:
        raise _error(409, "engine_unavailable", "Claude is unavailable on this server.")
    executable = resolve_claude_executable()
    if executable is None:
        raise _error(409, "engine_unavailable", "Install the Claude CLI on the server first.")
    try:
        return await rt.claude_login.start(owner, executable, folder)
    except (LoginError, OSError):
        raise _error(409, "login_unavailable", "Claude sign-in is busy or this account's folder is unavailable. Finish any pending sign-in and try again.") from None
