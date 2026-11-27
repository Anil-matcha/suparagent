"""The labels a support worker applied in Gmail and the undo answer (services/worker_labels.py). Counts and names only."""
from pydantic import BaseModel


class WorkerLabelCount(BaseModel):
    name: str
    count: int


class WorkerLabelSummary(BaseModel):
    agent_id: int
    applied: int
    threads: int
    labels: list[WorkerLabelCount]
    last_applied_at: str | None
    undoable: int
    accounts: list[str]


class WorkerLabelUndo(BaseModel):
    removed: int
    already_removed: int
    failed: int
