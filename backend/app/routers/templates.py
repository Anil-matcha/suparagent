"""Ready-made agent templates (`GET /templates`).

The reads are local and read-only: the bundled catalog (app/templates/catalog.py).
Nothing there creates an agent, calls a model or grants anything; the client
fills the new-agent form from a template and the person creates the agent.

`POST /templates/hire` is the one write: it creates agents from template ids
(see app/templates/hire.py). It grants nothing and switches nothing on.

`POST /templates/suggest` ranks bundled workers for a few stated goals
(see app/templates/suggest.py). It is read-only and creates nothing.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.routers.agents import get_runtime
from app.services import worker_suggestions, workspace_connections
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime
from app.templates import catalog, hire, suggest

router = APIRouter(
    prefix="/templates",
    tags=["Templates"],
    dependencies=[Depends(require_local_access)],
)


class TemplateSkillResponse(BaseModel):
    name: str
    description: str
    body: str


class TemplateRoutineResponse(BaseModel):
    id: str
    skill: str
    days: list[str]
    window: str
    budget_minutes: int
    note: str
    # Always False: a routine is described, never created, by choosing a template.
    enabled_after_install: bool


class TemplateResponse(BaseModel):
    id: str
    name: str
    role: str
    category: str
    description: str
    instructions: str
    color: str
    example_requests: list[str]
    requires: list[str]
    available: bool
    # Required connections that are not ready in this workspace (for example
    # "muapi" before it is connected). A role can still be added while this is
    # non-empty if every missing one can be connected in the app.
    missing: list[str]
    starter_notes: str | None
    skills: list[TemplateSkillResponse]
    routines: list[TemplateRoutineResponse]
    suggested_tools: list[str]
    setup_depth: str
    setup_text: str | None


class TemplateListResponse(BaseModel):
    version: int
    categories: list[str]
    templates: list[TemplateResponse]


def _response(template: catalog.Template, ready: frozenset[str]) -> TemplateResponse:
    return TemplateResponse(
        id=template.id,
        name=template.name,
        role=template.role,
        category=template.category,
        description=template.description,
        instructions=template.full_instructions,
        color=template.color,
        example_requests=list(template.example_requests),
        requires=list(template.requires),
        available=template.is_available(ready),
        missing=list(template.missing(ready)),
        starter_notes=template.starter_notes,
        skills=[TemplateSkillResponse(name=s.name, description=s.description, body=s.body) for s in template.skills],
        routines=[
            TemplateRoutineResponse(
                id=r.id,
                skill=r.skill,
                days=list(r.days),
                window=r.window,
                budget_minutes=r.budget_minutes,
                note=r.note,
                enabled_after_install=r.enabled_after_install,
            )
            for r in template.routines
        ],
        suggested_tools=list(template.suggested_tools),
        setup_depth=template.setup_depth,
        setup_text=template.setup_text,
    )


class HireRequest(BaseModel):
    # Template ids only: the server takes every other field from the catalog.
    template_ids: list[str] = Field(min_length=1, max_length=hire.MAX_HIRE)
    provider: str = Field(default="claude", min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=200)


class HiredRoutineResponse(BaseModel):
    template_routine_id: str
    routine_id: int
    name: str
    # Always True: a hired routine is created paused and never scheduled.
    paused: bool


class HiredWorkerResponse(BaseModel):
    template_id: str
    agent_id: int
    name: str
    skills: list[str]
    routines: list[HiredRoutineResponse]


class HireResponse(BaseModel):
    workers: list[HiredWorkerResponse]


@router.post("/hire", response_model=HireResponse, status_code=201)
async def hire_workers(payload: HireRequest, rt: ThreadRuntime = Depends(get_runtime)):
    """Add the chosen workers together, or none of them. A failure names the worker
    it happened on (`worker` in the error detail) and leaves nothing behind."""
    try:
        hired = await hire.hire(rt, payload.template_ids, provider=payload.provider, model=payload.model)
    except hire.HireError as exc:
        detail = {"error_code": exc.code, "message": exc.message}
        if exc.worker is not None:
            detail["worker"] = exc.worker
        raise HTTPException(status_code=exc.status, detail=detail) from None
    return HireResponse(
        workers=[
            HiredWorkerResponse(
                template_id=w.template_id,
                agent_id=w.agent_id,
                name=w.name,
                skills=list(w.skills),
                routines=[
                    HiredRoutineResponse(
                        template_routine_id=r.template_routine_id, routine_id=r.routine_id, name=r.name, paused=r.paused
                    )
                    for r in w.routines
                ],
            )
            for w in hired
        ]
    )


class UndoRequest(BaseModel):
    # The agent ids from a hire receipt.
    agent_ids: list[int] = Field(min_length=1, max_length=hire.MAX_HIRE)


class UndoResponse(BaseModel):
    removed: list[int]


@router.post("/hire/undo", response_model=UndoResponse)
async def undo_hire(payload: UndoRequest, rt: ThreadRuntime = Depends(get_runtime)):
    """Take back workers a hire just added. Only workers still exactly as the hire left
    them are removed (not edited, no conversation, routines paused and never run). If any
    is not, nothing is removed and `worker` names its agent id."""
    try:
        removed = await hire.undo(rt, payload.agent_ids)
    except hire.HireError as exc:
        detail = {"error_code": exc.code, "message": exc.message}
        if exc.worker is not None:
            detail["worker"] = exc.worker
        raise HTTPException(status_code=exc.status, detail=detail) from None
    return UndoResponse(removed=removed)


class SuggestGoalResponse(BaseModel):
    key: str
    label: str


class SuggestRequest(BaseModel):
    goals: list[str] = Field(default_factory=list, max_length=8)
    about: str = Field(default="", max_length=500)
    max_setup: str = Field(default="guided", pattern="^(express|standard|guided)$")
    limit: int = Field(default=5, ge=1, le=suggest.MAX_SUGGESTIONS)


class SuggestionResponse(BaseModel):
    template_id: str
    reason: str


class SuggestResponse(BaseModel):
    suggestions: list[SuggestionResponse]


@router.get("/suggest/goals", response_model=list[SuggestGoalResponse])
async def suggest_goals():
    return [SuggestGoalResponse(key=key, label=label) for key, (label, _) in suggest.GOALS.items()]


class LatestSuggestionResponse(BaseModel):
    suggestions: list[SuggestionResponse]
    created_at: float


@router.get("/suggest/latest", response_model=LatestSuggestionResponse | None)
async def latest_suggestion():
    """The team lead's latest fresh worker suggestion from a chat, or null. Read-only; only bundled ids are returned."""
    found = worker_suggestions.latest()
    if found is None:
        return None
    return LatestSuggestionResponse(
        suggestions=[SuggestionResponse(template_id=s["template_id"], reason=s["reason"]) for s in found["suggestions"]],
        created_at=found["created_at"],
    )


@router.post("/suggest", response_model=SuggestResponse)
async def suggest_workers(payload: SuggestRequest):
    """Rank bundled workers for the stated goals. Read-only: nothing is created or changed."""
    if any(len(goal) > 120 for goal in payload.goals):
        raise HTTPException(status_code=422, detail="Each goal must be 120 characters or fewer.")
    found = suggest.suggest(payload.goals, payload.about, max_setup=payload.max_setup, limit=payload.limit)
    return SuggestResponse(suggestions=[SuggestionResponse(template_id=f.template_id, reason=f.reason) for f in found])


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    q: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, max_length=40),
    db: AsyncSession = Depends(get_db),
):
    if category and category not in catalog.CATEGORIES:
        raise HTTPException(status_code=422, detail={"error_code": "unknown_category", "message": "Unknown category."})
    ready = await workspace_connections.ready_connections(db)
    return TemplateListResponse(
        version=catalog.CATALOG_VERSION,
        categories=list(catalog.CATEGORIES),
        templates=[_response(t, ready) for t in catalog.search(q, category, ready)],
    )


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(template_id: str, db: AsyncSession = Depends(get_db)):
    template = catalog.get(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail={"error_code": "unknown_template", "message": "No such template."})
    return _response(template, await workspace_connections.ready_connections(db))
