"""Owner-entered remote MCP servers: draft, test, enable, and exact per-agent tool grants.

Separate from the GitHub marketplace (`routers/community_mcp.py`): these rows describe an
endpoint the owner typed, not a reviewed listing. Secrets are write-only and live in the
encrypted credential store; a connection starts disabled and with no agent grants.
"""

import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import community_mcp as mcp
from app.core import crypto
from app.db.models import Agent, CustomMcpConnection, CustomMcpToolGrant, UserCredential
from app.db.session import get_db
from app.db.transactions import begin_write, release_snapshot
from app.routers.integrations import _owner
from app.schemas.custom_mcp import (
    CustomMcpConnectionOut,
    CustomMcpCreate,
    CustomMcpGrantRequest,
    CustomMcpRevision,
    CustomMcpToolOut,
    CustomMcpUpdate,
)
from app.services import user_credential_service
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import LOCAL_OWNER_ID

router = APIRouter(
    prefix="/integrations/custom-mcp",
    tags=["Custom MCP"],
    dependencies=[Depends(require_local_access), Depends(_owner)],
)

_CREDENTIAL_PREFIX = "integration:custom-mcp:"
_NO_FILE = "-"
_MAX_CONNECTIONS = 50
_HEADER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
_RESERVED_HEADERS = {
    "authorization", "host", "content-type", "content-length", "accept", "cookie", "set-cookie",
    "transfer-encoding", "connection", "upgrade", "te", "trailer", "user-agent", "origin",
    "referer", "expect", "keep-alive", "forwarded", "x-forwarded-for", "x-forwarded-host",
}
_RESERVED_PREFIXES = ("mcp-", "proxy-", "sec-")


def _http(exc: mcp.McpError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"error_code": exc.code, "message": str(exc)})


def _fail(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code})


# ---------------------------------------------------------------- validation

def _clean_name(value: str) -> str:
    name = " ".join(value.split())
    if not name or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise _fail(422, "invalid_name")
    return name


def _clean_endpoint(value: str) -> str:
    endpoint = value.strip()
    try:
        mcp._validate_public_url(endpoint, "endpoint", require_standard_port=True, max_length=300)
    except mcp.McpError as exc:
        raise _http(exc) from None
    return endpoint


def _clean_header_name(auth_mode: str, value: str | None) -> str | None:
    if auth_mode != "header":
        return None
    name = (value or "").strip()
    lowered = name.lower()
    if (not _HEADER_NAME.fullmatch(name) or lowered in _RESERVED_HEADERS
            or lowered.startswith(_RESERVED_PREFIXES)):
        raise _fail(422, "invalid_header_name")
    return name


def _clean_credential(auth_mode: str, secret) -> str | None:
    """Return a new secret, or None when blank/omitted (keep the saved one)."""
    if secret is None:
        return None
    value = secret.get_secret_value()
    if value == "":
        return None
    if auth_mode == "none":
        raise _fail(422, "credential_not_supported")
    if not 1 <= len(value) <= 4096 or not value.isascii() or any(not 32 <= ord(c) < 127 for c in value):
        raise _fail(422, "invalid_credential")
    return value


# ---------------------------------------------------------------- credentials

def _provider(connection_id: str) -> str:
    return _CREDENTIAL_PREFIX + connection_id


async def _credential(db: AsyncSession, connection_id: str) -> str | None:
    try:
        found = await user_credential_service.get_decrypted(db, LOCAL_OWNER_ID, _provider(connection_id))
        if found is None:
            return None
        value = json.loads(found[0])
        return value if isinstance(value, str) else None
    except Exception:  # noqa: BLE001 - an undecryptable secret is treated as absent
        return None
    finally:
        await release_snapshot(db)


async def _credential_configured(db: AsyncSession, connection_id: str) -> bool:
    return await db.scalar(select(UserCredential.provider).where(
        UserCredential.user_id == LOCAL_OWNER_ID, UserCredential.provider == _provider(connection_id),
    )) is not None


async def _save_credential(db: AsyncSession, connection_id: str, secret: str) -> None:
    row = await db.scalar(select(UserCredential).where(
        UserCredential.user_id == LOCAL_OWNER_ID, UserCredential.provider == _provider(connection_id),
    ).with_for_update())
    ciphertext = crypto.encrypt(json.dumps(secret))
    if row is None:
        db.add(UserCredential(
            user_id=LOCAL_OWNER_ID, provider=_provider(connection_id), file_path=_NO_FILE, ciphertext=ciphertext,
        ))
    else:
        row.file_path = _NO_FILE
        row.ciphertext = ciphertext


async def _delete_credential(db: AsyncSession, connection_id: str) -> None:
    await db.execute(delete(UserCredential).where(
        UserCredential.user_id == LOCAL_OWNER_ID, UserCredential.provider == _provider(connection_id),
    ))


# ---------------------------------------------------------------- rows

def _state(row: CustomMcpConnection) -> str:
    if row.last_error_code == "authentication_failed":
        return "needs_sign_in"
    if row.last_error_code:
        return "unreachable"
    if row.tested_revision != row.revision or row.tools_snapshot is None:
        return "not_tested"
    return "connected" if row.enabled else "disabled"


async def _out(db: AsyncSession, row: CustomMcpConnection) -> CustomMcpConnectionOut:
    return CustomMcpConnectionOut(
        id=row.id, name=row.name, transport=row.transport, endpoint=row.endpoint,
        auth_mode=row.auth_mode, auth_header_name=row.auth_header_name, enabled=row.enabled,
        revision=row.revision, state=_state(row), last_error_code=row.last_error_code,
        credential_configured=await _credential_configured(db, row.id),
        tool_count=None if row.tools_snapshot is None else len(row.tools_snapshot),
        last_tested_at=row.last_tested_at,
    )


async def _connection(db: AsyncSession, connection_id: str) -> CustomMcpConnection:
    row = await db.scalar(select(CustomMcpConnection).where(
        CustomMcpConnection.id == connection_id, CustomMcpConnection.owner_id == LOCAL_OWNER_ID,
    ))
    if row is None:
        raise _fail(404, "connection_not_found")
    return row


async def _locked(db: AsyncSession, connection_id: str, revision: int) -> CustomMcpConnection:
    """Row lock plus the revision check; call inside begin_write and the connection lock."""
    row = await db.scalar(select(CustomMcpConnection).where(
        CustomMcpConnection.id == connection_id, CustomMcpConnection.owner_id == LOCAL_OWNER_ID,
    ).with_for_update())
    if row is None:
        raise _fail(404, "connection_not_found")
    if row.revision != revision:
        raise _fail(409, "connection_changed")
    return row


def _bump(row: CustomMcpConnection) -> None:
    """New revision that keeps a still-valid test result valid."""
    still_tested = row.tested_revision == row.revision
    row.revision += 1
    if still_tested:
        row.tested_revision = row.revision


async def _delete_grants(db: AsyncSession, connection_id: str) -> None:
    await db.execute(delete(CustomMcpToolGrant).where(CustomMcpToolGrant.connection_id == connection_id))


def _invalidate(row: CustomMcpConnection) -> None:
    """The endpoint or sign-in changed: nothing learned about the old server still applies."""
    row.revision += 1
    row.enabled = False
    row.tested_revision = None
    row.tools_snapshot = None
    row.tools_digest = None
    row.last_error_code = None
    row.last_tested_at = None


def _tools_digest(tools: list[dict]) -> str:
    return mcp.digest(sorted((tool["name"], tool["schema_digest"]) for tool in tools))


async def _drop_stale_grants(db: AsyncSession, connection_id: str, tools: list[dict]) -> bool:
    current = {tool["name"]: tool["schema_digest"] for tool in tools}
    grants = (await db.scalars(select(CustomMcpToolGrant).where(
        CustomMcpToolGrant.connection_id == connection_id,
    ))).all()
    stale = [grant for grant in grants if current.get(grant.tool_name) != grant.schema_digest]
    for grant in stale:
        await db.delete(grant)
    return bool(stale)


async def _discover(endpoint: str, token: str | None, header: str | None) -> list[dict]:
    session = mcp.RemoteMcpSession(endpoint, token, header=header)
    try:
        tools = await session.list_tools()
    except mcp.McpError:
        raise
    except Exception:
        raise mcp.McpError("remote_unavailable", "The remote MCP server could not be reached.", 502) from None
    finally:
        await session.close()
    return mcp.reject_credential_in_metadata(tools, token)


async def _owned_agent(db: AsyncSession, agent_id: int) -> Agent:
    row = await db.scalar(select(Agent).where(
        Agent.id == agent_id, Agent.owner_id == LOCAL_OWNER_ID, Agent.archived_at.is_(None),
    ))
    if row is None:
        raise _fail(404, "agent_not_found")
    return row


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


# ---------------------------------------------------------------- routes

@router.get("/connections", response_model=list[CustomMcpConnectionOut])
async def list_connections(response: Response, db: AsyncSession = Depends(get_db)):
    _no_store(response)
    rows = (await db.scalars(
        select(CustomMcpConnection).where(CustomMcpConnection.owner_id == LOCAL_OWNER_ID)
        .order_by(CustomMcpConnection.created_at, CustomMcpConnection.id)
    )).all()
    return [await _out(db, row) for row in rows]


@router.post("/connections", response_model=CustomMcpConnectionOut, status_code=201)
async def create_connection(payload: CustomMcpCreate, response: Response, db: AsyncSession = Depends(get_db)):
    _no_store(response)
    name = _clean_name(payload.name)
    endpoint = _clean_endpoint(payload.endpoint)
    header = _clean_header_name(payload.auth_mode, payload.auth_header_name)
    secret = _clean_credential(payload.auth_mode, payload.credential)
    if secret is not None and secret in endpoint:
        raise _fail(422, "invalid_credential")
    await begin_write(db)
    count = len((await db.scalars(select(CustomMcpConnection.id).where(
        CustomMcpConnection.owner_id == LOCAL_OWNER_ID))).all())
    if count >= _MAX_CONNECTIONS:
        await db.rollback()
        raise _fail(409, "too_many_connections")
    row = CustomMcpConnection(
        id=str(uuid.uuid4()), owner_id=LOCAL_OWNER_ID, name=name, endpoint=endpoint,
        auth_mode=payload.auth_mode, auth_header_name=header, enabled=False,
    )
    db.add(row)
    if secret is not None:
        await db.flush()
        await _save_credential(db, row.id, secret)
    await db.commit()
    await db.refresh(row)
    return await _out(db, row)


@router.patch("/connections/{connection_id}", response_model=CustomMcpConnectionOut)
async def update_connection(
    connection_id: str, payload: CustomMcpUpdate, response: Response, db: AsyncSession = Depends(get_db),
):
    _no_store(response)
    current = await _connection(db, connection_id)
    auth_mode = payload.auth_mode or current.auth_mode
    header_input = payload.auth_header_name if payload.auth_header_name is not None else current.auth_header_name
    name = _clean_name(payload.name) if payload.name is not None else None
    endpoint = _clean_endpoint(payload.endpoint) if payload.endpoint is not None else current.endpoint
    header = _clean_header_name(auth_mode, header_input)
    secret = _clean_credential(auth_mode, payload.credential)
    if secret is not None and secret in endpoint:
        raise _fail(422, "invalid_credential")
    await release_snapshot(db)
    async with mcp.connection_lock(connection_id):
        await begin_write(db)
        row = await _locked(db, connection_id, payload.revision)
        target_changed = (endpoint, auth_mode, header) != (row.endpoint, row.auth_mode, row.auth_header_name)
        if target_changed or secret is not None:
            if secret is None and (endpoint != row.endpoint or auth_mode != row.auth_mode):
                # A saved secret never follows the connection to another URL or auth method.
                await _delete_credential(db, row.id)
            row.endpoint, row.auth_mode, row.auth_header_name = endpoint, auth_mode, header
            if name is not None:
                row.name = name
            if secret is not None:
                await _save_credential(db, row.id, secret)
            await _delete_grants(db, row.id)
            _invalidate(row)
        elif name is not None and name != row.name:
            row.name = name
            _bump(row)
        else:
            await db.rollback()
            return await _out(db, row)
        await db.commit()
        await db.refresh(row)
    return await _out(db, row)


@router.delete("/connections/{connection_id}/credential", response_model=CustomMcpConnectionOut)
async def remove_credential(
    connection_id: str, revision: int, response: Response, db: AsyncSession = Depends(get_db),
):
    _no_store(response)
    await _connection(db, connection_id)
    await release_snapshot(db)
    async with mcp.connection_lock(connection_id):
        await begin_write(db)
        row = await _locked(db, connection_id, revision)
        await _delete_credential(db, row.id)
        await _delete_grants(db, row.id)
        _invalidate(row)
        await db.commit()
        await db.refresh(row)
    return await _out(db, row)


@router.post("/connections/{connection_id}/test", response_model=CustomMcpConnectionOut)
async def test_connection(
    connection_id: str, payload: CustomMcpRevision, response: Response, db: AsyncSession = Depends(get_db),
):
    _no_store(response)
    row = await _connection(db, connection_id)
    if row.revision != payload.revision:
        raise _fail(409, "connection_changed")
    endpoint, auth_mode, header = row.endpoint, row.auth_mode, row.auth_header_name
    token = await _credential(db, connection_id)
    if auth_mode != "none" and token is None:
        raise _fail(409, "credential_required")
    failure: mcp.McpError | None = None
    tools: list[dict] = []
    try:
        tools = await _discover(endpoint, token if auth_mode != "none" else None,
                                header if auth_mode == "header" else None)
    except mcp.McpError as exc:
        failure = exc
    async with mcp.connection_lock(connection_id):
        await begin_write(db)
        current = await _locked(db, connection_id, payload.revision)
        if failure is not None:
            # A failed test never leaves a usable connection behind.
            current.enabled = False
            current.tested_revision = None
            current.tools_snapshot = None
            current.tools_digest = None
            current.last_error_code = failure.code
            current.last_tested_at = datetime.now(timezone.utc)
            current.revision += 1
            await db.commit()
            raise _http(failure)
        if await _drop_stale_grants(db, connection_id, tools):
            current.revision += 1
        current.tools_snapshot = tools
        current.tools_digest = _tools_digest(tools)
        current.tested_revision = current.revision
        current.last_error_code = None
        current.last_tested_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(current)
    return await _out(db, current)


@router.get("/connections/{connection_id}/tools", response_model=list[CustomMcpToolOut])
async def tools(connection_id: str, response: Response, db: AsyncSession = Depends(get_db)):
    """The tools seen by the last successful test. Never contacts the server."""
    _no_store(response)
    row = await _connection(db, connection_id)
    if row.tested_revision != row.revision or row.tools_snapshot is None:
        raise _fail(409, "connection_not_tested")
    return [CustomMcpToolOut(name=t["name"], description=t["description"],
                             input_schema=t["inputSchema"], schema_digest=t["schema_digest"])
            for t in row.tools_snapshot]


@router.post("/connections/{connection_id}/enable", response_model=CustomMcpConnectionOut)
async def enable_connection(
    connection_id: str, payload: CustomMcpRevision, response: Response, db: AsyncSession = Depends(get_db),
):
    _no_store(response)
    await _connection(db, connection_id)
    await release_snapshot(db)
    async with mcp.connection_lock(connection_id):
        await begin_write(db)
        row = await _locked(db, connection_id, payload.revision)
        if row.tested_revision != row.revision or row.tools_snapshot is None:
            await db.rollback()
            raise _fail(409, "connection_not_tested")
        row.enabled = True
        _bump(row)
        await db.commit()
        await db.refresh(row)
    return await _out(db, row)


@router.post("/connections/{connection_id}/disable", response_model=CustomMcpConnectionOut)
async def disable_connection(
    connection_id: str, payload: CustomMcpRevision, response: Response, db: AsyncSession = Depends(get_db),
):
    _no_store(response)
    await _connection(db, connection_id)
    await release_snapshot(db)
    async with mcp.connection_lock(connection_id):
        await begin_write(db)
        row = await _locked(db, connection_id, payload.revision)
        row.enabled = False
        _bump(row)
        await db.commit()
        await db.refresh(row)
    return await _out(db, row)


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(connection_id: str, response: Response, db: AsyncSession = Depends(get_db)):
    await _connection(db, connection_id)
    await release_snapshot(db)
    async with mcp.connection_lock(connection_id):
        await begin_write(db)
        row = await db.scalar(select(CustomMcpConnection).where(
            CustomMcpConnection.id == connection_id, CustomMcpConnection.owner_id == LOCAL_OWNER_ID,
        ).with_for_update())
        if row is None:
            raise _fail(404, "connection_not_found")
        await _delete_grants(db, row.id)
        await _delete_credential(db, row.id)
        await db.execute(delete(CustomMcpConnection).where(
            CustomMcpConnection.id == row.id, CustomMcpConnection.owner_id == LOCAL_OWNER_ID,
        ))
        await db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/connections/{connection_id}/agents/{agent_id}/grants")
async def list_grants(connection_id: str, agent_id: int, response: Response, db: AsyncSession = Depends(get_db)):
    _no_store(response)
    await _connection(db, connection_id)
    await _owned_agent(db, agent_id)
    rows = (await db.scalars(select(CustomMcpToolGrant).where(
        CustomMcpToolGrant.connection_id == connection_id, CustomMcpToolGrant.agent_id == agent_id,
    ).order_by(CustomMcpToolGrant.tool_name))).all()
    return {"tool_names": [r.tool_name for r in rows]}


@router.put("/connections/{connection_id}/agents/{agent_id}/grants")
async def set_grants(
    connection_id: str, agent_id: int, payload: CustomMcpGrantRequest,
    response: Response, db: AsyncSession = Depends(get_db),
):
    _no_store(response)
    row = await _connection(db, connection_id)
    await _owned_agent(db, agent_id)
    if row.revision != payload.revision:
        raise _fail(409, "connection_changed")
    if not row.enabled or row.tested_revision != row.revision:
        raise _fail(409, "connection_not_enabled")
    names = [selection.name for selection in payload.tools]
    if len(set(names)) != len(names):
        raise _fail(422, "invalid_tool_grant")
    endpoint, auth_mode, header = row.endpoint, row.auth_mode, row.auth_header_name
    token = await _credential(db, connection_id)
    if auth_mode != "none" and token is None:
        raise _fail(409, "credential_required")
    try:
        available = await _discover(endpoint, token if auth_mode != "none" else None,
                                    header if auth_mode == "header" else None)
    except mcp.McpError as exc:
        raise _http(exc) from None
    by_name = {tool["name"]: tool for tool in available}
    async with mcp.agent_grant_lock(agent_id):
        async with mcp.connection_lock(connection_id):
            await begin_write(db)
            current = await _locked(db, connection_id, payload.revision)
            if not current.enabled:
                await db.rollback()
                raise _fail(409, "connection_not_enabled")
            agent = await db.scalar(select(Agent).where(
                Agent.id == agent_id, Agent.owner_id == LOCAL_OWNER_ID, Agent.archived_at.is_(None),
            ).with_for_update())
            if agent is None:
                await db.rollback()
                raise _fail(409, "agent_changed")
            if await _drop_stale_grants(db, connection_id, available):
                current.revision += 1
                current.tested_revision = current.revision
            drifted = any(
                selection.name not in by_name or by_name[selection.name]["schema_digest"] != selection.schema_digest
                for selection in payload.tools
            )
            if drifted:
                await db.commit()
                raise _fail(409, "tool_schema_changed")
            size = len(mcp.canonical([
                {"name": n, "description": by_name[n]["description"], "inputSchema": by_name[n]["inputSchema"]}
                for n in names
            ]).encode("utf-8"))
            if len(names) > mcp.MAX_TOOLS or size > mcp.MAX_TOOLSET_BYTES:
                await db.rollback()
                raise _fail(422, "agent_toolset_too_large")
            await db.execute(delete(CustomMcpToolGrant).where(
                CustomMcpToolGrant.connection_id == connection_id, CustomMcpToolGrant.agent_id == agent_id,
            ))
            for name in names:
                tool = by_name[name]
                db.add(CustomMcpToolGrant(
                    agent_id=agent_id, connection_id=connection_id, tool_name=name,
                    schema_digest=tool["schema_digest"], description=tool["description"],
                    input_schema=tool["inputSchema"],
                ))
            await db.commit()
    return {"tool_names": names}
