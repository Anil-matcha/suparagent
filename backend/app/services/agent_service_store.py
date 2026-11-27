"""Cleanup shared by the custom MCP and agent-service routers (kept here so neither imports the other's routes)."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentServiceAssignment, AgentServiceProvider, AgentServiceResource


async def remove_provider_data(db: AsyncSession, connection_id: str) -> None:
    """Drop the designation, resources and assignments of a connection. Call inside a write transaction."""
    resource_ids = select(AgentServiceResource.id).where(AgentServiceResource.connection_id == connection_id)
    await db.execute(delete(AgentServiceAssignment).where(AgentServiceAssignment.resource_id.in_(resource_ids)))
    await db.execute(delete(AgentServiceResource).where(AgentServiceResource.connection_id == connection_id))
    await db.execute(delete(AgentServiceProvider).where(AgentServiceProvider.connection_id == connection_id))


async def provider_resources(db: AsyncSession, agent_id: int, connection_ids: list[str]) -> dict[str, tuple[str, list[str]]]:
    """For each of these connections that is a mailbox/number provider: (provider name, addresses assigned to the agent).

    A connection nobody marked as a provider is absent. An empty address list means the agent has none."""
    if not connection_ids:
        return {}
    providers = {row.connection_id: row.provider for row in (await db.scalars(select(AgentServiceProvider).where(
        AgentServiceProvider.connection_id.in_(connection_ids)))).all()}
    if not providers:
        return {}
    rows = (await db.execute(
        select(AgentServiceResource.connection_id, AgentServiceResource.address)
        .join(AgentServiceAssignment, AgentServiceAssignment.resource_id == AgentServiceResource.id)
        .where(AgentServiceAssignment.agent_id == agent_id, AgentServiceResource.connection_id.in_(list(providers)))
        .order_by(AgentServiceResource.created_at, AgentServiceResource.id)
    )).all()
    found: dict[str, list[str]] = {connection_id: [] for connection_id in providers}
    for connection_id, address in rows:
        found[connection_id].append(address)
    return {connection_id: (providers[connection_id], found[connection_id]) for connection_id in providers}


async def unassigned_providers(db: AsyncSession, agent_id: int, connection_ids: list[str]) -> set[str]:
    """Of these connections, the ones that are mailbox/number providers where the agent has no resource.

    A connection nobody marked as a provider is never in the result. Used at mount time and again on
    every call, so removing an assignment stops a running turn at its next call."""
    found = await provider_resources(db, agent_id, connection_ids)
    return {connection_id for connection_id, (_provider, addresses) in found.items() if not addresses}


async def provider_identifiers(db: AsyncSession, agent_id: int, connection_ids: list[str]) -> dict[str, list[str]]:
    """The provider's own ids (for AgentPhone, the number ids) of the resources assigned to the agent, per connection.

    Resources recorded without a provider id are left out, so a call that names a number only by id cannot match them."""
    if not connection_ids:
        return {}
    rows = (await db.execute(
        select(AgentServiceResource.connection_id, AgentServiceResource.provider_resource_id)
        .join(AgentServiceAssignment, AgentServiceAssignment.resource_id == AgentServiceResource.id)
        .where(AgentServiceAssignment.agent_id == agent_id, AgentServiceResource.connection_id.in_(connection_ids),
               AgentServiceResource.provider_resource_id.is_not(None))
    )).all()
    found: dict[str, list[str]] = {}
    for connection_id, identifier in rows:
        found.setdefault(connection_id, []).append(identifier)
    return found
