from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime

WORKER_LABEL_STATUSES = ("applied", "failed", "outcome_unknown", "undone")


class WorkerLabel(Base):
    """A label the app itself created in a connected Gmail account, always under the `Worker/` prefix (BL-173).

    The only labels the label tools will ever use or remove: a label whose name starts with `Worker/` but is not
    in this table was made by someone else and is refused."""

    __tablename__ = "worker_label"
    __table_args__ = (
        Index("uq_worker_label_account_name", "account_id", "name", unique=True),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    gmail_label_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)


class WorkerLabelAction(Base):
    """One label applied to one thread by an agent, kept so the owner can see it and remove it again in one click.

    `status` is one of WORKER_LABEL_STATUSES (checked in the service, not the database). An `outcome_unknown` is
    never repeated automatically. `turn_id` and `run_id` carry no foreign key so pruning a turn or run never
    deletes the record."""

    __tablename__ = "worker_label_action"
    __table_args__ = (
        Index("ix_worker_label_action_agent_applied_at", "agent_id", "applied_at"),
        Index("ix_worker_label_action_account_thread", "account_id", "thread_id"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agent.id", name="fk_worker_label_action_agent_id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    thread_id: Mapped[str] = mapped_column(String, nullable=False)
    label_name: Mapped[str] = mapped_column(String, nullable=False)
    gmail_label_id: Mapped[str] = mapped_column(String, nullable=False)
    turn_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    request_key: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    undone_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
