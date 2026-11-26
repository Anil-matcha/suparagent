"""Learning from the owner's past replies (services/history_learning.py): the opt-in, and the lessons waiting for review.
Plain fields only: no mail, no ids, no read cursor."""
from pydantic import BaseModel, ConfigDict


class HistoryAccount(BaseModel):
    account_id: str
    enabled: bool
    declined: bool
    query: str
    max_threads: int
    months: int
    allowed: int
    threads_read: int
    stage_done: bool
    finished: bool
    started_at: str | None
    last_read_at: str | None


class HistoryLearning(BaseModel):
    agent_id: int
    accounts: list[HistoryAccount]
    pending: int
    accepted: int


class HistoryAccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    declined: bool | None = None
    query: str | None = None
    max_threads: int | None = None
    months: int | None = None
    extend: int | None = None
    restart: bool = False


class Lesson(BaseModel):
    id: str
    type: str
    pattern: str
    text: str
    count: int
    newest: str
    oldest: str
    status: str
    destination: str | None = None
    entry_id: str | None = None
    created_at: float | None = None
    decided_at: float | None = None


class Lessons(BaseModel):
    pending: list[Lesson]
    accepted: list[Lesson]


class LessonAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = None


class HistoryForget(BaseModel):
    entries_removed: int
    lessons_removed: int
