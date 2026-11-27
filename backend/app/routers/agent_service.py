"""Agent-owned mailboxes and phone numbers: provider designation, resources and agent assignment.

The provider is an owner-entered custom MCP connection (`routers/custom_mcp.py`), which keeps the
endpoint, sign-in and tool grants. This router adds only what the owner holds at that provider and
which agents may be given it. Nothing here grants a tool: a new provider, resource or agent has no
assignment, and an assignment alone lets an agent call nothing.
"""

import re
import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Agent,
    AgentServiceAssignment,
    AgentServiceAudit,
    AgentServiceProvider,
    AgentServiceResource,
    CustomMcpConnection,
    CustomMcpToolGrant,
)
from app.db.session import get_db
from app import community_mcp
from app.db.transactions import begin_write, release_snapshot
from app.routers import custom_mcp
from app.routers.custom_mcp import _bind_actor, _clean_name, _connection, _fail, _no_store, _owned_agent
from app.routers.integrations import _owner
from app.schemas.custom_mcp import CustomMcpCreate
from app.schemas.agent_service import (
    AgentServiceCandidateOut,
    AgentServiceAssignRequest,
    AgentServiceAuditOut,
    AgentServicePresetOut,
    AgentServiceProviderFromPreset,
    AgentServiceProviderOut,
    AgentServiceProviderSet,
    AgentServiceResourceCreate,
    AgentServiceResourceOut,
)
from app.services import agent_service_policy
from app.services.agent_service_store import remove_provider_data
from app.services.local_access_policy import require_local_access

router = APIRouter(
    prefix="/integrations/agent-services",
    tags=["Agent services"],
    dependencies=[Depends(require_local_access), Depends(_owner), Depends(_bind_actor)],
)

def _owner_id() -> str:
    """The workspace owner, read from the custom MCP router so both always agree."""
    return custom_mcp.LOCAL_OWNER_ID


_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+$")
_PHONE = re.compile(r"^\+[1-9][0-9]{6,14}$")
# A named provider only holds one kind of resource; "other" may hold either.
_KIND_OF = {"agentmail": "email", "agentphone": "phone"}


def _clean_address(kind: str, value: str) -> str:
    address = value.strip()
    if kind == "email":
        address = address.lower()
        if not _EMAIL.fullmatch(address) or len(address) > 200:
            raise _fail(422, "invalid_address")
    elif not _PHONE.fullmatch(address):  # E.164, so every number is stored in one form
        raise _fail(422, "invalid_address")
    return address


async def _visible_connection_ids(db: AsyncSession) -> set[str]:
    """Connections the acting person may see (private ones belong to their creator)."""
    actor = custom_mcp._actor_var.get()
    rows = (await db.scalars(select(CustomMcpConnection).where(CustomMcpConnection.owner_id == _owner_id()))).all()
    return {row.id for row in rows if custom_mcp._can_manage(row, actor)}


async def _provider(db: AsyncSession, connection_id: str) -> AgentServiceProvider:
    await _connection(db, connection_id)  # 404 for a missing or someone else's private connection
    row = await db.get(AgentServiceProvider, connection_id)
    if row is None:
        raise _fail(404, "provider_not_found")
    return row


async def _resource(db: AsyncSession, resource_id: str) -> AgentServiceResource:
    row = await db.scalar(select(AgentServiceResource).where(
        AgentServiceResource.id == resource_id, AgentServiceResource.owner_id == _owner_id(),
    ))
    if row is None or row.connection_id not in await _visible_connection_ids(db):
        raise _fail(404, "resource_not_found")
    return row


async def _resource_out(db: AsyncSession, row: AgentServiceResource) -> AgentServiceResourceOut:
    agent_ids = (await db.scalars(select(AgentServiceAssignment.agent_id).where(
        AgentServiceAssignment.resource_id == row.id,
    ).order_by(AgentServiceAssignment.agent_id))).all()
    return AgentServiceResourceOut(
        id=row.id, connection_id=row.connection_id, kind=row.kind, address=row.address, label=row.label,
        provider_resource_id=row.provider_resource_id, agent_ids=list(agent_ids), created_at=row.created_at,
    )


async def _provider_out(db: AsyncSession, row: AgentServiceProvider) -> AgentServiceProviderOut:
    connection = await db.get(CustomMcpConnection, row.connection_id)
    count = await db.scalar(select(func.count()).select_from(AgentServiceResource).where(
        AgentServiceResource.connection_id == row.connection_id,
    ))
    return AgentServiceProviderOut(
        connection_id=row.connection_id, connection_name=connection.name if connection else "",
        provider=row.provider, resource_limit=row.resource_limit, resource_count=int(count or 0),
        created_at=row.created_at,
    )


# ---------------------------------------------------------------- presets

# The services' official hosted MCP endpoints, with browser sign-in. Endpoints come from the reviewed OpenMausBot
# guide (docs/custom-mcp-servers.md, pin 1a4e4da9) and are not yet confirmed against a live account here.
_PRESETS = {
    "agentmail": AgentServicePresetOut(
        preset="agentmail", label="AgentMail", kind="email", endpoint="https://mcp.agentmail.to/mcp",
        summary="Email inboxes for an agent, on your own AgentMail account."),
    "agentphone": AgentServicePresetOut(
        preset="agentphone", label="AgentPhone", kind="phone", endpoint="https://mcp.agentphone.ai/mcp",
        summary="US and Canadian numbers, texts and calls for an agent, on your own AgentPhone account."),
}


@router.get("/presets", response_model=list[AgentServicePresetOut])
async def list_presets(response: Response):
    _no_store(response)
    return list(_PRESETS.values())


@router.post("/providers", response_model=AgentServiceProviderOut, status_code=201)
async def add_provider_from_preset(
    payload: AgentServiceProviderFromPreset, response: Response, db: AsyncSession = Depends(get_db),
):
    """Create a disabled, private custom MCP connection to the preset's official endpoint (browser sign-in) and
    mark it as a provider. The owner then signs in, tests and enables it with the custom MCP routes; nothing is
    enabled, granted or assigned here."""
    _no_store(response)
    preset = _PRESETS[payload.preset]
    created = await custom_mcp.create_connection(
        CustomMcpCreate(name=payload.name or preset.label, endpoint=preset.endpoint, auth_mode="oauth"),
        response, db)
    await begin_write(db)
    row = AgentServiceProvider(connection_id=created.id, provider=preset.preset, resource_limit=payload.resource_limit)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return await _provider_out(db, row)


# ---------------------------------------------------------------- providers

@router.get("/providers", response_model=list[AgentServiceProviderOut])
async def list_providers(response: Response, db: AsyncSession = Depends(get_db)):
    _no_store(response)
    visible = await _visible_connection_ids(db)
    rows = (await db.scalars(select(AgentServiceProvider).order_by(AgentServiceProvider.created_at))).all()
    return [await _provider_out(db, row) for row in rows if row.connection_id in visible]


@router.put("/providers/{connection_id}", response_model=AgentServiceProviderOut)
async def set_provider(
    connection_id: str, payload: AgentServiceProviderSet, response: Response, db: AsyncSession = Depends(get_db),
):
    """Mark a connection as a provider, or change its kind or cap. Never lowers the cap below what exists."""
    _no_store(response)
    await _connection(db, connection_id)
    await begin_write(db)
    row = await db.get(AgentServiceProvider, connection_id)
    count = int(await db.scalar(select(func.count()).select_from(AgentServiceResource).where(
        AgentServiceResource.connection_id == connection_id)) or 0)
    if payload.resource_limit < count:
        await db.rollback()
        raise _fail(409, "limit_below_existing")
    expected = _KIND_OF.get(payload.provider)
    if expected is not None and await db.scalar(select(func.count()).select_from(AgentServiceResource).where(
            AgentServiceResource.connection_id == connection_id, AgentServiceResource.kind != expected)):
        await db.rollback()
        raise _fail(409, "provider_kind_mismatch")
    if row is None:
        row = AgentServiceProvider(connection_id=connection_id, provider=payload.provider,
                                   resource_limit=payload.resource_limit)
        db.add(row)
    else:
        row.provider, row.resource_limit = payload.provider, payload.resource_limit
    await db.commit()
    await db.refresh(row)
    return await _provider_out(db, row)


@router.delete("/providers/{connection_id}", status_code=204)
async def clear_provider(connection_id: str, response: Response, db: AsyncSession = Depends(get_db)):
    """Stop treating the connection as a provider. Its resources, assignments and agent tool grants go
    with it, so agents do not quietly keep using it as an ordinary connection; the connection stays and
    the audit record is kept. A running turn stops at its next call (the revision changes)."""
    await _provider(db, connection_id)
    await begin_write(db)
    await remove_provider_data(db, connection_id)
    await db.execute(delete(CustomMcpToolGrant).where(CustomMcpToolGrant.connection_id == connection_id))
    connection = await db.scalar(select(CustomMcpConnection).where(
        CustomMcpConnection.id == connection_id, CustomMcpConnection.owner_id == _owner_id()).with_for_update())
    if connection is not None:
        custom_mcp._bump(connection)
    await db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/providers/{connection_id}/lookup", response_model=list[AgentServiceCandidateOut])
async def look_up(connection_id: str, response: Response, db: AsyncSession = Depends(get_db)):
    """Read-only: ask the service which mailboxes or numbers the signed-in account has, so the owner can pick one
    instead of typing its id. Uses the owner's own sign-in; no agent is involved and nothing is changed."""
    _no_store(response)
    provider = await _provider(db, connection_id)
    tool = agent_service_policy.lookup_tool(provider.provider)
    if tool is None:
        raise _fail(422, "lookup_not_supported")
    row = await _connection(db, connection_id)
    endpoint = row.endpoint
    token, header = await custom_mcp._auth_for(db, row)
    have = {address for address in (await db.scalars(select(AgentServiceResource.address).where(
        AgentServiceResource.connection_id == connection_id))).all()}
    await release_snapshot(db)
    session = community_mcp.RemoteMcpSession(endpoint, token, header=header)
    try:
        result = await session.call_tool(tool, {"limit": 100})
    except community_mcp.McpError as exc:
        raise custom_mcp._http(exc) from None
    except Exception:
        raise _fail(502, "remote_unavailable") from None
    finally:
        await session.close()
    found = agent_service_policy.candidates(provider.provider, result)
    if found is None:
        raise _fail(502, "lookup_unreadable")
    return [AgentServiceCandidateOut(**item, added=item["address"].lower() in {a.lower() for a in have}) for item in found]


# ---------------------------------------------------------------- resources

@router.get("/resources", response_model=list[AgentServiceResourceOut])
async def list_resources(response: Response, db: AsyncSession = Depends(get_db)):
    _no_store(response)
    visible = await _visible_connection_ids(db)
    rows = (await db.scalars(select(AgentServiceResource).where(
        AgentServiceResource.owner_id == _owner_id(),
    ).order_by(AgentServiceResource.created_at, AgentServiceResource.id))).all()
    return [await _resource_out(db, row) for row in rows if row.connection_id in visible]


@router.post("/resources", response_model=AgentServiceResourceOut, status_code=201)
async def create_resource(payload: AgentServiceResourceCreate, response: Response, db: AsyncSession = Depends(get_db)):
    """Record a mailbox or number the owner holds at a provider. It starts assigned to no agent."""
    _no_store(response)
    provider = await _provider(db, payload.connection_id)
    expected = _KIND_OF.get(provider.provider)
    if expected is not None and payload.kind != expected:
        raise _fail(422, "kind_not_supported_by_provider")
    address = _clean_address(payload.kind, payload.address)
    label = _clean_name(payload.label)
    identifier = (payload.provider_resource_id or "").strip() or None
    if provider.provider == "agentphone" and identifier is None:
        # AgentPhone names a number by id in most tools; without the id none of those calls could match it.
        raise _fail(422, "provider_resource_id_required")
    await begin_write(db)
    # Re-read the cap inside the write so two requests cannot both pass it.
    current = await db.get(AgentServiceProvider, payload.connection_id)
    if current is None:
        await db.rollback()
        raise _fail(404, "provider_not_found")
    count = int(await db.scalar(select(func.count()).select_from(AgentServiceResource).where(
        AgentServiceResource.connection_id == payload.connection_id)) or 0)
    if count >= current.resource_limit:
        await db.rollback()
        raise _fail(409, "resource_limit_reached")
    if await db.scalar(select(AgentServiceResource.id).where(
            AgentServiceResource.connection_id == payload.connection_id,
            AgentServiceResource.kind == payload.kind, AgentServiceResource.address == address)):
        await db.rollback()
        raise _fail(409, "resource_exists")
    row = AgentServiceResource(
        id=str(uuid.uuid4()), owner_id=_owner_id(), connection_id=payload.connection_id, kind=payload.kind,
        address=address, label=label, provider_resource_id=identifier,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return await _resource_out(db, row)


@router.delete("/resources/{resource_id}", status_code=204)
async def delete_resource(resource_id: str, response: Response, db: AsyncSession = Depends(get_db)):
    await _resource(db, resource_id)
    await begin_write(db)
    await db.execute(delete(AgentServiceAssignment).where(AgentServiceAssignment.resource_id == resource_id))
    await db.execute(delete(AgentServiceResource).where(
        AgentServiceResource.id == resource_id, AgentServiceResource.owner_id == _owner_id()))
    await db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.put("/resources/{resource_id}/agents", response_model=AgentServiceResourceOut)
async def set_assignment(
    resource_id: str, payload: AgentServiceAssignRequest, response: Response, db: AsyncSession = Depends(get_db),
):
    """Replace the set of agents this resource is assigned to. An empty list removes every assignment."""
    _no_store(response)
    row = await _resource(db, resource_id)
    agent_ids = sorted(set(payload.agent_ids))
    for agent_id in agent_ids:
        await _owned_agent(db, agent_id)
    await begin_write(db)
    # The resource may have been removed while the agents were checked.
    if await db.get(AgentServiceResource, resource_id) is None:
        await db.rollback()
        raise _fail(404, "resource_not_found")
    await db.execute(delete(AgentServiceAssignment).where(AgentServiceAssignment.resource_id == resource_id))
    for agent_id in agent_ids:
        db.add(AgentServiceAssignment(agent_id=agent_id, resource_id=resource_id))
    await db.commit()
    return await _resource_out(db, row)


@router.get("/agents/{agent_id}/resources", response_model=list[AgentServiceResourceOut])
async def resources_of_agent(agent_id: int, response: Response, db: AsyncSession = Depends(get_db)):
    _no_store(response)
    await _owned_agent(db, agent_id)
    visible = await _visible_connection_ids(db)
    rows = (await db.scalars(select(AgentServiceResource).join(
        AgentServiceAssignment, AgentServiceAssignment.resource_id == AgentServiceResource.id,
    ).where(
        AgentServiceAssignment.agent_id == agent_id, AgentServiceResource.owner_id == _owner_id(),
    ).order_by(AgentServiceResource.created_at, AgentServiceResource.id))).all()
    return [await _resource_out(db, row) for row in rows if row.connection_id in visible]


# ---------------------------------------------------------------- audit

@router.get("/audit", response_model=list[AgentServiceAuditOut])
async def list_audit(
    response: Response, agent_id: int | None = None, limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000), db: AsyncSession = Depends(get_db),
):
    """Newest first: which agent used which address or number through which provider, and how it ended.
    Holds no message text or arguments. Calls through a private connection are visible only to its creator
    (and, once the connection is gone, to the workspace owner)."""
    _no_store(response)
    actor = custom_mcp._actor_var.get()
    visible = await _visible_connection_ids(db)
    existing = set((await db.scalars(select(CustomMcpConnection.id).where(
        CustomMcpConnection.owner_id == _owner_id()))).all())
    query = select(AgentServiceAudit).where(AgentServiceAudit.owner_id == _owner_id())
    if agent_id is not None:
        query = query.where(AgentServiceAudit.agent_id == agent_id)
    rows = (await db.scalars(query.order_by(AgentServiceAudit.created_at.desc(), AgentServiceAudit.id))).all()
    shown = [row for row in rows if row.connection_id in visible
             or (row.connection_id not in existing and (actor is None or actor.owner_like))]
    return [AgentServiceAuditOut(
        id=row.id, agent_id=row.agent_id, agent_name=row.agent_name, connection_id=row.connection_id,
        connection_name=row.connection_name, provider=row.provider, tool_name=row.tool_name,
        resources=[str(r) for r in (row.resources or [])], outcome=row.outcome, approved_by=row.approved_by,
        created_at=row.created_at,
    ) for row in shown[offset:offset + limit]]
