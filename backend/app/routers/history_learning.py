"""Learning from the owner's past replies (BL-174), for the owner to switch on and to review.

GET    /api/v1/agents/{id}/history-learning                       the opt-in per connected Gmail account
PUT    /api/v1/agents/{id}/history-learning/{account_id}          turn it on or off, set the support search, allow more
DELETE /api/v1/agents/{id}/history-learning                       forget everything learned from history
GET    /api/v1/agents/{id}/lessons                                what the worker learned, waiting and used
POST   /api/v1/agents/{id}/lessons/{lesson_id}/accept             use it (answer library) or suggest it (policy)
POST   /api/v1/agents/{id}/lessons/{lesson_id}/dismiss            drop it; it is never proposed again

Only the owner or an admin may use them: they decide what mail the worker may read and what it keeps. The worker has no
tool for any of these. What the worker itself calls is in composio_tools.py (gmail_read_sent_replies, lessons_propose)."""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.middleware import principal_of
from app.composio_tools import LEVELS
from app.routers.agents import get_runtime
from app.schemas.history_learning import (HistoryAccountUpdate, HistoryForget, HistoryLearning, Lesson, LessonAccept,
                                         Lessons, HistoryAccount)
from app.services import agent_memory, composio_bridge, history_learning, memory_topics
from app.services import agent_store as store
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(
    prefix="/agents",
    tags=["History learning"],
    dependencies=[Depends(require_local_access)],
)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


def _admin_only(request: Request) -> None:
    principal = principal_of(request.scope)
    if principal is not None and not principal.is_admin:  # a paired phone without admin access
        raise _error(403, "history_learning_requires_admin", "Learning from past replies can only be managed from the web app.")


async def _agent(rt: ThreadRuntime, agent_id: int):
    async with rt.session() as db:
        agent = await store.get_agent(db, agent_id)
    if agent is None or agent.owner_id != rt.owner_id or agent.archived_at is not None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


def _gmail_accounts(agent) -> list[str]:
    """The Gmail accounts the agent is selected for (empty when Gmail is off for it)."""
    if composio_bridge.apps_for(agent.connector_scopes).get("gmail") not in LEVELS:
        return []
    return composio_bridge.accounts_for(agent.connector_account_scopes).get("gmail", [])


def _summary(agent) -> HistoryLearning:
    lessons = history_learning.list_lessons(agent.id)
    return HistoryLearning(agent_id=agent.id, accounts=history_learning.settings_list(agent.id, _gmail_accounts(agent)),
                           pending=len(lessons["pending"]), accepted=len(lessons["accepted"]))


@router.get("/{agent_id}/history-learning", response_model=HistoryLearning)
async def get_history_learning(agent_id: int, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _admin_only(request)
    return _summary(await _agent(rt, agent_id))


@router.put("/{agent_id}/history-learning/{account_id}", response_model=HistoryAccount)
async def put_history_learning(agent_id: int, account_id: str, payload: HistoryAccountUpdate, request: Request,
                               rt: ThreadRuntime = Depends(get_runtime)):
    _admin_only(request)
    agent = await _agent(rt, agent_id)
    switching_on = payload.enabled is True or payload.extend is not None or payload.restart or payload.query is not None
    if switching_on and account_id not in _gmail_accounts(agent):
        raise _error(422, "agent_cannot_read_mail", "Give the agent Gmail access to that account first.")
    try:
        return history_learning.update_settings(agent.id, account_id, enabled=payload.enabled, declined=payload.declined,
                                                query=payload.query, max_threads=payload.max_threads, months=payload.months,
                                                extend=payload.extend, restart=payload.restart)
    except history_learning.HistoryRefused as exc:
        raise _error(422, exc.code, str(exc)) from None


@router.delete("/{agent_id}/history-learning", response_model=HistoryForget)
async def forget_history_learning(agent_id: int, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _admin_only(request)
    agent = await _agent(rt, agent_id)
    try:
        return history_learning.forget(agent.id)
    except agent_memory.MemoryConflict:
        raise _error(409, "notes_changed", "The notes changed while this ran. Try again.") from None


@router.get("/{agent_id}/lessons", response_model=Lessons)
async def get_lessons(agent_id: int, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _admin_only(request)
    return history_learning.list_lessons((await _agent(rt, agent_id)).id)


@router.post("/{agent_id}/lessons/{lesson_id}/accept", response_model=Lesson)
async def accept_lesson(agent_id: int, lesson_id: str, request: Request, payload: LessonAccept | None = None,
                        rt: ThreadRuntime = Depends(get_runtime)):
    _admin_only(request)
    agent = await _agent(rt, agent_id)
    try:
        return history_learning.accept_lesson(agent.id, lesson_id, text=(payload.text if payload else None))
    except history_learning.LessonNotFound:
        raise HTTPException(status_code=404, detail="No such lesson") from None
    except history_learning.HistoryRefused as exc:
        raise _error(409, exc.code, str(exc)) from None
    except agent_memory.MemoryTooLarge as exc:
        raise _error(413, "memory_too_large", str(exc)) from None
    except memory_topics.TooManyTopics as exc:
        raise _error(409, "too_many_topics", str(exc)) from None
    except agent_memory.MemoryConflict:
        raise _error(409, "notes_changed", "The answer library changed while this ran. Try again.") from None


@router.post("/{agent_id}/lessons/{lesson_id}/dismiss", status_code=204)
async def dismiss_lesson(agent_id: int, lesson_id: str, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    _admin_only(request)
    agent = await _agent(rt, agent_id)
    try:
        history_learning.dismiss_lesson(agent.id, lesson_id)
    except history_learning.LessonNotFound:
        raise HTTPException(status_code=404, detail="No such lesson") from None
