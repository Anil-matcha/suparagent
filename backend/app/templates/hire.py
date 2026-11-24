"""Hire ready-made workers in one step (`POST /templates/hire`).

The caller sends template ids only. Everything else (name, role, instructions,
skills, starter notes, starter routines) comes from the bundled catalog, so a
client can never push its own text through this path.

Each worker becomes a new agent that asks first, holds no connected-app grants
and has no folder of the caller's choosing. Its starter routines are created
paused with no schedule: the person sets days and a window when switching one on.

The hire is all or nothing. Agents and routines are written in one database
transaction. An agent's skills and notes are files, which a transaction cannot
cover, so they are written before the commit and removed again if anything
fails. A failure names the worker it happened on and leaves nothing behind.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger
from app.db.models.agent import Agent
from app.db.models.routine import Routine, RoutineRun
from app.db.models.thread import Thread
from app.db.transactions import begin_write
from app.services import agent_memory, agent_skills, workspace_connections
from app.services import agent_store as store
from app.services import routine_service
from app.services.agent_removal import remove_agent
from app.services.thread_runtime import EngineModelInvalid, ThreadRuntime, UnknownProvider
from app.templates import catalog

log = get_logger(__name__)

MAX_HIRE = 8
ROUTINE_NAME_CHARS = 80
ROUTINE_ACRONYMS = frozenset({"seo", "ai", "faq", "crm", "kpi", "roi"})


class HireError(Exception):
    """A refused or failed hire. `worker` is the template id it concerns, if any."""

    def __init__(self, status: int, code: str, message: str, worker: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.worker = worker


@dataclass
class HiredRoutine:
    template_routine_id: str
    routine_id: int
    name: str
    paused: bool


@dataclass
class HiredWorker:
    template_id: str
    agent_id: int
    name: str
    skills: list[str] = field(default_factory=list)
    routines: list[HiredRoutine] = field(default_factory=list)


def _routine_name(routine: catalog.TemplateRoutine) -> str:
    words = routine.id.split("-")
    # Sentence case, but keep known acronyms in capitals ("seo-keyword-brief" -> "SEO keyword brief").
    text = " ".join(word.upper() if word in ROUTINE_ACRONYMS else word for word in words)
    return (text[:1].upper() + text[1:])[:ROUTINE_NAME_CHARS]


def _routine_prompt(template: catalog.Template, routine: catalog.TemplateRoutine) -> str:
    return (
        f"Run your {routine.skill} skill now, following the shared run rules in your instructions. "
        f"Schedule note: {routine.note}"
    )


def _plan(template_ids: list[str], ready: frozenset[str]) -> list[catalog.Template]:
    """The templates to hire, checked before anything is written."""
    if not template_ids:
        raise HireError(400, "no_workers", "Choose at least one worker.")
    if len(template_ids) > MAX_HIRE:
        raise HireError(400, "too_many_workers", f"Hire at most {MAX_HIRE} workers at once.")
    if len(set(template_ids)) != len(template_ids):
        raise HireError(400, "duplicate_worker", "Each worker can be chosen once per hire.")
    templates: list[catalog.Template] = []
    for template_id in template_ids:
        template = catalog.get(template_id)
        if template is None:
            raise HireError(404, "unknown_template", "No such worker.", template_id)
        if not template.is_available(ready):
            raise HireError(
                400,
                "template_unavailable",
                f"{template.name} needs a connection this app cannot set up yet.",
                template_id,
            )
        templates.append(template)
    return templates


def _forget_files(agent_ids: list[int]) -> None:
    """Remove the skills and notes written for agents whose rows were rolled back."""
    for agent_id in agent_ids:
        agent_skills.remove_all(agent_id)
        agent_memory.remove_memory(agent_id)


async def hire(
    rt: ThreadRuntime, template_ids: list[str], *, provider: str = "claude", model: str | None = None
) -> list[HiredWorker]:
    """Create one agent per template, with its skills, notes and paused routines, or nothing."""
    try:
        rt.check_engine_model(provider, model)
    except UnknownProvider:
        raise HireError(422, "unknown_provider", f"Unknown engine: {provider}") from None
    except EngineModelInvalid as exc:
        raise HireError(422, "unknown_model", str(exc)) from None

    hired: list[HiredWorker] = []
    current: str | None = None
    async with rt.session() as db:
        templates = _plan(template_ids, await workspace_connections.ready_connections(db))
        await begin_write(db)
        try:
            for template in templates:
                current = template.id
                now = datetime.now(UTC)
                agent = Agent(
                    owner_id=rt.owner_id,
                    name=await store.free_name(db, rt.owner_id, template.name),
                    title=template.role,
                    description=template.description,
                    instructions=template.full_instructions,
                    provider=provider,
                    model=model,
                    approval_mode="ask",
                    created_at=now,
                    updated_at=now,
                )
                db.add(agent)
                await db.flush()
                worker = HiredWorker(template_id=template.id, agent_id=agent.id, name=agent.name)
                hired.append(worker)
                # Files first: a failure here still rolls the whole hire back.
                if template.starter_notes and template.starter_notes.strip():
                    agent_memory.write_memory(agent.id, template.starter_notes)
                else:
                    agent_memory.remove_memory(agent.id)
                for skill in template.skills:
                    agent_skills.write_skill(
                        agent.id, skill.name, skill.description, skill.body,
                        enabled=True, source=f"template:{template.id}",
                    )
                    worker.skills.append(skill.name)
                for routine in template.routines:
                    row = await routine_service.create_routine(
                        db,
                        rt.owner_id,
                        {
                            "agent_id": agent.id,
                            "name": _routine_name(routine),
                            "prompt": _routine_prompt(template, routine),
                            "schedule_kind": "manual",
                            "schedule_spec": {},
                            "paused": True,
                            "approval_mode": "ask",
                        },
                        commit=False,
                    )
                    worker.routines.append(
                        HiredRoutine(routine.id, row.id, row.name, bool(row.paused))
                    )
            await db.commit()
        except HireError:
            await db.rollback()
            _forget_files([w.agent_id for w in hired])
            raise
        except IntegrityError:
            await db.rollback()
            _forget_files([w.agent_id for w in hired])
            raise HireError(
                409, "agent_name_taken", "A worker with that name was just added. Try again.", current
            ) from None
        except routine_service.InvalidRoutine as exc:
            await db.rollback()
            _forget_files([w.agent_id for w in hired])
            raise HireError(422, exc.code, str(exc), current) from None
        except agent_skills.SkillError as exc:
            await db.rollback()
            _forget_files([w.agent_id for w in hired])
            raise HireError(exc.status, exc.code, exc.message, current) from None
        except Exception:
            log.exception("hiring workers failed", extra={"worker": current})
            await db.rollback()
            _forget_files([w.agent_id for w in hired])
            raise HireError(500, "hire_failed", "The workers could not be added.", current) from None
    return hired


async def _undo_blocker(db, owner_id: str, agent_id: int) -> tuple[str, str] | None:
    """Why this hired worker can no longer be undone, as (code, message), or None."""
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.owner_id != owner_id or agent.archived_at is not None:
        return ("worker_missing", "That worker is already gone or was archived.")
    if agent.updated_at != agent.created_at:
        return ("worker_changed", f"{agent.name} was edited after it was added, so it was left in place.")
    chats = await db.scalar(select(func.count()).select_from(Thread).where(Thread.agent_id == agent_id))
    if chats:
        return ("worker_in_use", f"{agent.name} already has conversations, so it was left in place.")
    routines = (await db.scalars(select(Routine).where(Routine.agent_id == agent_id))).all()
    for routine in routines:
        if not routine.paused:
            return ("worker_in_use", f"A routine of {agent.name} was switched on, so it was left in place.")
        runs = await db.scalar(
            select(func.count()).select_from(RoutineRun).where(RoutineRun.routine_id == routine.id)
        )
        if runs:
            return ("worker_in_use", f"A routine of {agent.name} has already run, so it was left in place.")
    return None


async def undo(rt: ThreadRuntime, agent_ids: list[int]) -> list[int]:
    """Remove workers a hire just added, or none of them.

    A worker is removed only while it is exactly as the hire left it: not edited,
    no conversation, and every routine still paused and never run. If any worker
    in the list fails that test, nothing is removed and the error names it."""
    if not agent_ids:
        raise HireError(400, "no_workers", "Nothing to undo.")
    if len(set(agent_ids)) != len(agent_ids):
        raise HireError(400, "duplicate_worker", "Each worker can be listed once.")
    async with rt.session() as db:
        for agent_id in agent_ids:
            blocker = await _undo_blocker(db, rt.owner_id, agent_id)
            if blocker is not None:
                raise HireError(409, blocker[0], blocker[1], str(agent_id))
        await begin_write(db)
        for agent_id in agent_ids:
            rows = (await db.scalars(select(Routine).where(Routine.agent_id == agent_id))).all()
            for routine in rows:
                await routine_service.delete_routine(db, rt.owner_id, routine.id, commit=False)
        await db.commit()
    for agent_id in agent_ids:
        await remove_agent(rt, agent_id)
    return list(agent_ids)
