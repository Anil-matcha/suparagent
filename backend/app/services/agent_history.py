"""Stored versions of an agent's editable profile (BL-128).

Each edit that changes something adds a revision holding the profile after the change; the first edit
of an agent with no history also stores the profile as it was before (the "baseline") so there is
something to go back to. Text that looks like a secret is redacted in the snapshot and a restore
leaves those fields as they are. Only the newest KEEP revisions per agent are kept.
"""

from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AgentRevision
from app.services.agent_memory import redact_json

KEEP = 20

# What a revision captures: the fields a person edits in the profile. Archiving, the lead role and
# the team are not part of it.
PROFILE_FIELDS = (
    "name", "title", "description", "instructions", "provider", "model", "effort", "workspace_path",
    "approval_mode", "pinned", "memory_enabled", "recall_enabled", "memory_upkeep", "compaction_summary",
    "use_helper", "fallback_provider", "fallback_model", "connector_scopes", "connector_account_scopes",
)


class RevisionNotFound(Exception):
    pass


def profile_of(agent: Agent) -> dict:
    return {field: getattr(agent, field) for field in PROFILE_FIELDS}


def changed_fields(before: dict, after: dict) -> list[str]:
    return [field for field in PROFILE_FIELDS if before.get(field) != after.get(field)]


def _stored(profile: dict) -> tuple[dict, list[str]]:
    safe = redact_json(profile)
    return safe, [field for field in PROFILE_FIELDS if safe.get(field) != profile.get(field)]


async def latest_number(db: AsyncSession, agent_id: int) -> int:
    return (await db.execute(select(func.max(AgentRevision.number)).where(AgentRevision.agent_id == agent_id))).scalar() or 0


async def _add(db: AsyncSession, agent_id: int, owner_id: str, number: int, source: str, profile: dict,
               changed: list[str], restored_from: int | None = None) -> None:
    snapshot, redacted = _stored(profile)
    db.add(AgentRevision(agent_id=agent_id, number=number, source=source, actor=owner_id,
                         changed_fields=changed, redacted=redacted, restored_from=restored_from,
                         snapshot=snapshot, created_at=datetime.now(UTC)))


async def record(db: AsyncSession, agent: Agent, before: dict, *, source: str = "edit",
                 restored_from: int | None = None) -> int | None:
    """Add the revision for a change just made to `agent` (not yet committed; the caller holds the
    agent row lock). Returns its number, or None when nothing in the profile changed."""
    after = profile_of(agent)
    changed = changed_fields(before, after)
    if not changed:
        return None
    number = await latest_number(db, agent.id)
    if number == 0:
        await _add(db, agent.id, agent.owner_id, 1, "baseline", before, [])
        number = 1
    number += 1
    await _add(db, agent.id, agent.owner_id, number, source, after, changed, restored_from)
    await db.flush()
    await db.execute(delete(AgentRevision).where(AgentRevision.agent_id == agent.id,
                                                 AgentRevision.number <= number - KEEP))
    return number


async def list_revisions(db: AsyncSession, agent_id: int) -> list[AgentRevision]:
    return list((await db.execute(select(AgentRevision).where(AgentRevision.agent_id == agent_id)
                                  .order_by(AgentRevision.number.desc()))).scalars())


async def get_revision(db: AsyncSession, agent_id: int, number: int) -> AgentRevision:
    row = (await db.execute(select(AgentRevision).where(AgentRevision.agent_id == agent_id,
                                                        AgentRevision.number == number))).scalar_one_or_none()
    if row is None:
        raise RevisionNotFound(number)
    return row


def restorable(revision: AgentRevision, agent: Agent) -> dict:
    """The fields a restore would set: the snapshot minus redacted fields and what already matches."""
    skip = set(revision.redacted or [])
    current = profile_of(agent)
    fields = {f: v for f, v in revision.snapshot.items()
              if f in PROFILE_FIELDS and f not in skip and current.get(f) != v}
    # A new engine resets the model and effort unless they are given, so give the stored ones.
    for follows in ("provider", "model") if "provider" in fields else ():
        for field in ("model", "effort"):
            if field in revision.snapshot and field not in skip:
                fields.setdefault(field, revision.snapshot[field])
    return fields
