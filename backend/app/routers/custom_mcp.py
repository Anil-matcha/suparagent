"""Owner-entered remote MCP servers: draft, test, enable, and exact per-agent tool grants.

Separate from the GitHub marketplace (`routers/community_mcp.py`): these rows describe an
endpoint the owner typed, not a reviewed listing. Secrets are write-only and live in the
encrypted credential store; a connection starts disabled and with no agent grants.
"""

import json
import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import community_mcp as mcp
from app import custom_mcp_oauth as oauth
from app import custom_mcp_oauth_loopback as loopback
from app.core import crypto
from app.core.config import settings
from app.auth.middleware import principal_of
from app.db.models import Agent, CustomMcpConnection, CustomMcpOAuthFlow, CustomMcpToolGrant, Person, UserCredential
from app.db.session import get_db
from app.db.transactions import begin_write, release_snapshot
from app.desktop import profile
from app.routers.integrations import _owner
from app.schemas.custom_mcp import (
    CustomMcpConnectionOut,
    CustomMcpCreate,
    CustomMcpGrantRequest,
    CustomMcpOAuthStart,
    CustomMcpRevision,
    CustomMcpSharing,
    CustomMcpToolOut,
    CustomMcpUpdate,
)
from app.services import user_credential_service
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import LOCAL_OWNER_ID

@dataclass(frozen=True)
class Actor:
    """Who is acting. `person_id` is None for the workspace owner and for non-member principals
    (the desktop app, a paired device, a trusted local request)."""
    person_id: int | None

    @property
    def owner_like(self) -> bool:
        return self.person_id is None


# Bound once per request by `_bind_actor`. Unset (internal calls, the desktop loopback sign-in) means no
# per-person check: that path is proven by the one-time sign-in state instead.
_actor_var: ContextVar[Actor | None] = ContextVar("custom_mcp_actor", default=None)


async def _bind_actor(request: Request) -> None:
    principal = principal_of(request.scope)
    _actor_var.set(Actor(person_id=getattr(principal, "person_id", None)))


router = APIRouter(
    prefix="/integrations/custom-mcp",
    tags=["Custom MCP"],
    dependencies=[Depends(require_local_access), Depends(_owner), Depends(_bind_actor)],
)


def _can_manage(row: CustomMcpConnection, actor: Actor | None) -> bool:
    """A shared connection is managed by any admin; a private one only by the person who added it."""
    return actor is None or row.shared or row.owner_person_id == actor.person_id


def _allowed(row: CustomMcpConnection, *, removal: bool = False) -> bool:
    actor = _actor_var.get()
    # The workspace owner can always switch off or remove a connection (for example when a member leaves),
    # but never use, test, sign in to or grant tools from someone else's private one.
    return _can_manage(row, actor) or (removal and actor is not None and actor.owner_like)

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


def _clean_oauth(auth_mode: str, client_id: str | None, scopes: str | None) -> dict | None:
    """The owner's own OAuth settings. Discovered endpoints are added when sign-in starts."""
    if auth_mode != "oauth":
        return None
    client = (client_id or "").strip()
    scope = " ".join((scopes or "").split())
    if client and (len(client) > 200 or not client.isascii() or any(not 32 < ord(c) < 127 for c in client)):
        raise _fail(422, "invalid_oauth_client")
    if len(scope) > oauth.MAX_SCOPE_LENGTH or not scope.isascii() or any(not 32 <= ord(c) < 127 for c in scope):
        raise _fail(422, "invalid_oauth_scopes")
    return {"client_id": client or None, "scope": scope or None}


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

def _state(row: CustomMcpConnection, signed_in: bool = True) -> str:
    if row.auth_mode == "oauth" and not signed_in:
        return "needs_sign_in"
    if row.last_error_code == "authentication_failed":
        return "needs_sign_in"
    if row.last_error_code:
        return "unreachable"
    if row.tested_revision != row.revision or row.tools_snapshot is None:
        return "not_tested"
    return "connected" if row.enabled else "disabled"


async def _out(db: AsyncSession, row: CustomMcpConnection) -> CustomMcpConnectionOut:
    signed_in = row.auth_mode == "oauth" and await oauth.is_signed_in(db, LOCAL_OWNER_ID, row.id)
    settings_ = row.oauth_config or {}
    actor = _actor_var.get()
    person = await db.get(Person, row.owner_person_id) if row.owner_person_id is not None else None
    added_by = person.display_name if person is not None else "Workspace owner"
    return CustomMcpConnectionOut(
        id=row.id, name=row.name, transport=row.transport, endpoint=row.endpoint,
        auth_mode=row.auth_mode, auth_header_name=row.auth_header_name, enabled=row.enabled,
        revision=row.revision, state=_state(row, signed_in), last_error_code=row.last_error_code,
        credential_configured=await _credential_configured(db, row.id), oauth_signed_in=signed_in,
        oauth_client_id=settings_.get("client_id"), oauth_scopes=settings_.get("scope"),
        server_name=row.server_name, server_version=row.server_version,
        shared=row.shared, mine=actor is None or row.owner_person_id == actor.person_id,
        can_manage=_can_manage(row, actor), added_by=added_by,
        tool_count=None if row.tools_snapshot is None else len(row.tools_snapshot),
        last_tested_at=row.last_tested_at,
    )


async def _connection(db: AsyncSession, connection_id: str, *, removal: bool = False) -> CustomMcpConnection:
    row = await db.scalar(select(CustomMcpConnection).where(
        CustomMcpConnection.id == connection_id, CustomMcpConnection.owner_id == LOCAL_OWNER_ID,
    ))
    if row is None or not _allowed(row, removal=removal):
        raise _fail(404, "connection_not_found")  # someone else's private connection looks absent, not forbidden
    return row


async def _locked(db: AsyncSession, connection_id: str, revision: int, *, removal: bool = False) -> CustomMcpConnection:
    """Row lock plus the revision check; call inside begin_write and the connection lock."""
    row = await db.scalar(select(CustomMcpConnection).where(
        CustomMcpConnection.id == connection_id, CustomMcpConnection.owner_id == LOCAL_OWNER_ID,
    ).with_for_update())
    if row is None or not _allowed(row, removal=removal):
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
    row.server_name = None
    row.server_version = None


def _tools_digest(tools: list[dict]) -> str:
    return mcp.digest(sorted((tool["name"], tool["schema_digest"]) for tool in tools))


async def _delete_flows(db: AsyncSession, connection_id: str) -> None:
    await db.execute(delete(CustomMcpOAuthFlow).where(CustomMcpOAuthFlow.connection_id == connection_id))


async def _drop_stale_grants(db: AsyncSession, connection_id: str, tools: list[dict]) -> bool:
    current = {tool["name"]: tool["schema_digest"] for tool in tools}
    grants = (await db.scalars(select(CustomMcpToolGrant).where(
        CustomMcpToolGrant.connection_id == connection_id,
    ))).all()
    stale = [grant for grant in grants if current.get(grant.tool_name) != grant.schema_digest]
    for grant in stale:
        await db.delete(grant)
    return bool(stale)


async def _discover(endpoint: str, token: str | None, header: str | None) -> tuple[list[dict], dict | None]:
    """The server's tools and what it calls itself (display only)."""
    session = mcp.RemoteMcpSession(endpoint, token, header=header)
    session.max_tools, session.max_toolset_bytes = mcp.CUSTOM_MAX_TOOLS, mcp.CUSTOM_MAX_TOOLSET_BYTES
    try:
        tools = await session.list_tools()
        identity = session.server_info
    except mcp.McpError:
        raise
    except Exception:
        raise mcp.McpError("remote_unavailable", "The remote MCP server could not be reached.", 502) from None
    finally:
        await session.close()
    return mcp.reject_credential_in_metadata(tools, token), identity


async def _auth_for(db: AsyncSession, row: CustomMcpConnection) -> tuple[str | None, str | None]:
    """(secret to send, header name or None for Bearer) for this connection, or an HTTP error."""
    if row.auth_mode == "none":
        return None, None
    if row.auth_mode == "oauth":
        factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
        await release_snapshot(db)
        try:
            return await oauth.access_token(LOCAL_OWNER_ID, row.id, factory), None
        except oauth.OAuthError as exc:
            raise _http(exc) from None
    token = await _credential(db, row.id)
    if token is None:
        raise _fail(409, "credential_required")
    return token, row.auth_header_name if row.auth_mode == "header" else None


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
    actor = _actor_var.get()
    query = select(CustomMcpConnection).where(CustomMcpConnection.owner_id == LOCAL_OWNER_ID)
    if actor is not None and not actor.owner_like:
        query = query.where(or_(CustomMcpConnection.shared.is_(True), CustomMcpConnection.owner_person_id == actor.person_id))
    rows = (await db.scalars(query.order_by(CustomMcpConnection.created_at, CustomMcpConnection.id))).all()
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
    oauth_config = _clean_oauth(payload.auth_mode, payload.oauth_client_id, payload.oauth_scopes)
    await begin_write(db)
    count = len((await db.scalars(select(CustomMcpConnection.id).where(
        CustomMcpConnection.owner_id == LOCAL_OWNER_ID))).all())
    if count >= _MAX_CONNECTIONS:
        await db.rollback()
        raise _fail(409, "too_many_connections")
    row = CustomMcpConnection(
        id=str(uuid.uuid4()), owner_id=LOCAL_OWNER_ID, name=name, endpoint=endpoint,
        auth_mode=payload.auth_mode, auth_header_name=header, enabled=False, oauth_config=oauth_config,
        owner_person_id=(_actor_var.get().person_id if _actor_var.get() is not None else None), shared=False,
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
    previous = current.oauth_config or {}
    new_oauth = _clean_oauth(
        auth_mode,
        payload.oauth_client_id if payload.oauth_client_id is not None else previous.get("client_id"),
        payload.oauth_scopes if payload.oauth_scopes is not None else previous.get("scope"),
    )
    oauth_changed = (new_oauth or {}).get("client_id") != previous.get("client_id") or (new_oauth or {}).get("scope") != previous.get("scope")
    await release_snapshot(db)
    async with mcp.connection_lock(connection_id):
        await begin_write(db)
        row = await _locked(db, connection_id, payload.revision)
        target_changed = (endpoint, auth_mode, header) != (row.endpoint, row.auth_mode, row.auth_header_name) or oauth_changed
        if target_changed or secret is not None:
            if secret is None and (endpoint != row.endpoint or auth_mode != row.auth_mode):
                # A saved secret never follows the connection to another URL or auth method.
                await _delete_credential(db, row.id)
            row.endpoint, row.auth_mode, row.auth_header_name = endpoint, auth_mode, header
            row.oauth_config = new_oauth
            # Whatever was signed in belonged to the old address, client or secret.
            await oauth.delete_record(db, LOCAL_OWNER_ID, row.id)
            await _delete_flows(db, row.id)
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
    endpoint = row.endpoint
    token, header = await _auth_for(db, row)
    failure: mcp.McpError | None = None
    tools: list[dict] = []
    identity: dict | None = None
    try:
        tools, identity = await _discover(endpoint, token, header)
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
            current.server_name = current.server_version = None
            current.last_error_code = failure.code
            current.last_tested_at = datetime.now(timezone.utc)
            current.revision += 1
            await db.commit()
            raise _http(failure)
        if await _drop_stale_grants(db, connection_id, tools):
            current.revision += 1
        current.tools_snapshot = tools
        current.tools_digest = _tools_digest(tools)
        current.server_name = (identity or {}).get("name")
        current.server_version = (identity or {}).get("version")
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
    await _connection(db, connection_id, removal=True)
    await release_snapshot(db)
    async with mcp.connection_lock(connection_id):
        await begin_write(db)
        row = await _locked(db, connection_id, payload.revision, removal=True)
        row.enabled = False
        _bump(row)
        await db.commit()
        await db.refresh(row)
    return await _out(db, row)


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(connection_id: str, response: Response, db: AsyncSession = Depends(get_db)):
    await _connection(db, connection_id, removal=True)
    await release_snapshot(db)
    async with mcp.connection_lock(connection_id):
        await begin_write(db)
        row = await db.scalar(select(CustomMcpConnection).where(
            CustomMcpConnection.id == connection_id, CustomMcpConnection.owner_id == LOCAL_OWNER_ID,
        ).with_for_update())
        if row is None or not _allowed(row, removal=True):
            raise _fail(404, "connection_not_found")
        await _delete_grants(db, row.id)
        await _delete_credential(db, row.id)
        await oauth.delete_record(db, LOCAL_OWNER_ID, row.id)
        await _delete_flows(db, row.id)
        await db.execute(delete(CustomMcpConnection).where(
            CustomMcpConnection.id == row.id, CustomMcpConnection.owner_id == LOCAL_OWNER_ID,
        ))
        await db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/connections/{connection_id}/sharing", response_model=CustomMcpConnectionOut)
async def set_sharing(
    connection_id: str, payload: CustomMcpSharing, response: Response, db: AsyncSession = Depends(get_db),
):
    """Let the whole workspace use this connection, or take it back. Sharing is the creator's decision."""
    _no_store(response)
    actor = _actor_var.get()
    row = await _connection(db, connection_id, removal=True)
    is_creator = actor is None or row.owner_person_id == actor.person_id
    if payload.shared and not is_creator:
        raise _fail(403, "only_the_creator_can_share")
    if not payload.shared and not (is_creator or (actor is not None and actor.owner_like)):
        raise _fail(403, "only_the_creator_can_unshare")
    await release_snapshot(db)
    async with mcp.connection_lock(connection_id):
        await begin_write(db)
        current = await _locked(db, connection_id, payload.revision, removal=True)
        if current.shared == payload.shared:
            await db.rollback()
            return await _out(db, current)
        current.shared = payload.shared
        _bump(current)  # a running turn rechecks the revision, so a change takes effect on its next call
        await db.commit()
        await db.refresh(current)
    return await _out(db, current)


def _callback_url(request: Request) -> str:
    origin = settings.PUBLIC_ORIGIN or f"{request.url.scheme}://{request.url.netloc}"
    return origin + request.url_for("custom_mcp_oauth_callback").path


@router.post("/connections/{connection_id}/oauth/start", response_model=CustomMcpOAuthStart)
async def oauth_start(
    connection_id: str, payload: CustomMcpRevision, request: Request, response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Find the sign-in server, register this app if needed, and return the page to open."""
    _no_store(response)
    row = await _connection(db, connection_id)
    if row.revision != payload.revision:
        raise _fail(409, "connection_changed")
    if row.auth_mode != "oauth":
        raise _fail(409, "oauth_not_selected")
    endpoint, config = row.endpoint, dict(row.oauth_config or {})
    client_secret = await _credential(db, connection_id)
    receiver = None
    if profile.active():
        # The desktop API needs the launch token, which a system browser cannot send: return to a
        # one-shot loopback listener instead (see custom_mcp_oauth_loopback).
        factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)

        async def finish(state: str, code: str, error: str) -> tuple[int, bytes, dict[str, str]]:
            async with factory() as session:
                page = await _complete_sign_in(session, state, code, error)
            return page.status_code, bytes(page.body), dict(page.headers)

        receiver = await loopback.open_receiver(connection_id, finish, oauth.FLOW_TTL_SECONDS)
        redirect_uri = receiver.redirect_uri
    else:
        redirect_uri = _callback_url(request)
    try:
        server = await oauth.discover(endpoint)
        manual = config.get("client_id")
        reuse = (not manual and config.get("effective_client_id") and config.get("redirect_uri") == redirect_uri
                 and config.get("server", {}).get("issuer") == server["issuer"])
        registered_secret = None
        if manual:
            client_id = manual
        elif reuse:
            client_id = config["effective_client_id"]
        else:
            client = await oauth.register_client(server, redirect_uri)
            client_id, registered_secret = client["client_id"], client["client_secret"]
    except mcp.McpError as exc:
        if receiver is not None:
            await receiver.close()
        raise _http(exc) from None
    verifier, challenge = oauth.new_pkce()
    state, digest = oauth.new_state()
    url = oauth.authorization_url(server, client_id=client_id, redirect_uri=redirect_uri, state=state,
                                  challenge=challenge, scope=config.get("scope"))
    try:
        return await _store_flow(db, connection_id, payload.revision, server=server, client_id=client_id,
                                 registered_secret=registered_secret, config=config, redirect_uri=redirect_uri,
                                 url=url, verifier=verifier, digest=digest)
    except BaseException:
        if receiver is not None:
            await receiver.close()
        raise


async def _store_flow(db, connection_id, revision, *, server, client_id, registered_secret, config, redirect_uri,
                      url, verifier, digest) -> CustomMcpOAuthStart:
    async with mcp.connection_lock(connection_id):
        await begin_write(db)
        current = await _locked(db, connection_id, revision)
        if registered_secret is not None:
            await _save_credential(db, connection_id, registered_secret)
        current.oauth_config = {**config, "server": server, "effective_client_id": client_id, "redirect_uri": redirect_uri}
        now = datetime.now(timezone.utc)
        await db.execute(delete(CustomMcpOAuthFlow).where(
            CustomMcpOAuthFlow.connection_id == connection_id, CustomMcpOAuthFlow.expires_at <= now))
        open_flows = (await db.scalars(select(CustomMcpOAuthFlow).where(
            CustomMcpOAuthFlow.connection_id == connection_id).order_by(CustomMcpOAuthFlow.created_at))).all()
        for old in open_flows[:-4]:
            await db.delete(old)
        db.add(CustomMcpOAuthFlow(
            state_digest=digest, connection_id=connection_id, owner_id=LOCAL_OWNER_ID,
            person_id=(_actor_var.get().person_id if _actor_var.get() is not None else None),
            connection_revision=current.revision, verifier_cipher=crypto.encrypt(verifier),
            redirect_uri=redirect_uri, expires_at=now + timedelta(seconds=oauth.FLOW_TTL_SECONDS)))
        await db.commit()
    return CustomMcpOAuthStart(authorization_url=url)


def _finished(ok: bool, message: str, status: int = 200) -> HTMLResponse:
    title = "Signed in" if ok else "Sign-in did not finish"
    body = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><body style='font:16px system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
            f"<h1 style='font-size:1.25rem'>{title}</h1><p>{message}</p>"
            f"<p>You can close this tab and return to Suparagent.</p>")
    return HTMLResponse(body, status_code=status, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    })


async def _complete_sign_in(db: AsyncSession, state: str, code: str, error: str) -> HTMLResponse:
    """Finish one sign-in. `state` is one-time and tied to the signed-in owner who started it."""
    digest = oauth.state_digest(state)
    if digest is None:
        return _finished(False, "This sign-in link is not valid.", 400)
    await begin_write(db)
    flow = await db.scalar(select(CustomMcpOAuthFlow).where(
        CustomMcpOAuthFlow.state_digest == digest, CustomMcpOAuthFlow.owner_id == LOCAL_OWNER_ID).with_for_update())
    if flow is None:
        await db.rollback()
        return _finished(False, "This sign-in link is not valid or was already used.", 400)
    actor = _actor_var.get()
    if actor is not None and flow.person_id != actor.person_id:
        # Started by someone else: their sign-in cannot be finished in this person's browser.
        await db.rollback()
        return _finished(False, "This sign-in link is not valid or was already used.", 400)
    snapshot = (flow.connection_id, flow.connection_revision, flow.verifier_cipher, flow.redirect_uri)
    expired = flow.expires_at <= datetime.now(timezone.utc)
    await db.delete(flow)  # one use, even when the provider reported an error
    await db.commit()
    if expired:
        return _finished(False, "The sign-in took too long. Start again from Suparagent.", 400)
    if error or not code or len(code) > 4096:
        return _finished(False, "The sign-in was cancelled or refused.", 400)
    connection_id, revision, verifier_cipher, redirect_uri = snapshot
    row = await _connection(db, connection_id)
    config = row.oauth_config or {}
    if row.revision != revision or row.auth_mode != "oauth" or "server" not in config:
        return _finished(False, "This connection changed while you were signing in. Start again.", 409)
    client_secret = await _credential(db, connection_id)
    try:
        verifier = crypto.decrypt(verifier_cipher)
        record = await oauth.exchange_code(
            config["server"], client_id=config["effective_client_id"], client_secret=client_secret,
            code=code, verifier=verifier, redirect_uri=redirect_uri)
    except Exception:  # noqa: BLE001 - fixed wording; never repeat what the server said
        return _finished(False, "The server did not accept the sign-in.", 502)
    try:
        async with mcp.connection_lock(connection_id):
            await begin_write(db)
            current = await _locked(db, connection_id, revision)
            await oauth.save_record(db, LOCAL_OWNER_ID, connection_id, record, client_secret)
            await _delete_grants(db, connection_id)  # a new sign-in may be a different account
            _invalidate(current)
            await db.commit()
    except HTTPException:
        return _finished(False, "This connection changed while you were signing in. Start again.", 409)
    return _finished(True, "Now test the connection in Suparagent to finish.")


@router.get("/oauth/callback", name="custom_mcp_oauth_callback")
async def oauth_callback(state: str = "", code: str = "", error: str = "", db: AsyncSession = Depends(get_db)):
    """Where the provider sends the browser back (browser and self-hosted installs)."""
    return await _complete_sign_in(db, state, code, error)


@router.post("/connections/{connection_id}/oauth/sign-out", response_model=CustomMcpConnectionOut)
async def oauth_sign_out(
    connection_id: str, payload: CustomMcpRevision, response: Response, db: AsyncSession = Depends(get_db),
):
    _no_store(response)
    row = await _connection(db, connection_id)
    if row.revision != payload.revision:
        raise _fail(409, "connection_changed")
    config = dict(row.oauth_config or {})
    stored = await db.scalar(select(UserCredential.ciphertext).where(
        UserCredential.user_id == LOCAL_OWNER_ID, UserCredential.provider == oauth.provider(connection_id)))
    secret = await _credential(db, connection_id)
    await release_snapshot(db)
    unpacked = oauth.unpack(stored) if stored else None
    if unpacked and "server" in config:
        await oauth.revoke(config["server"], client_id=config.get("effective_client_id", ""), client_secret=secret, record=unpacked[0])
    async with mcp.connection_lock(connection_id):
        await begin_write(db)
        current = await _locked(db, connection_id, payload.revision)
        await oauth.delete_record(db, LOCAL_OWNER_ID, connection_id)
        await _delete_flows(db, connection_id)
        await _delete_grants(db, connection_id)
        _invalidate(current)
        await db.commit()
        await db.refresh(current)
    return await _out(db, current)


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
    endpoint = row.endpoint
    token, header = await _auth_for(db, row)
    try:
        available, _identity = await _discover(endpoint, token, header)
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
            if len(names) > mcp.CUSTOM_MAX_TOOLS or size > mcp.CUSTOM_MAX_TOOLSET_BYTES:
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
