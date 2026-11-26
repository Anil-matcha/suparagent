from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime

SHADOW_FEEDBACK_KINDS = ("draft", "ranking")
SHADOW_FEEDBACK_REASONS = ("wrong_tone", "wrong_policy", "missing_context", "wrong_priority", "other")


class ShadowFeedback(Base):
    """The owner's "that was wrong, and why" about one thread the worker predicted in shadow mode (BL-175).

    One row per agent, account, thread and kind, so changing your mind replaces the row. The reason comes from a
    fixed list (checked in the service, not the database) so it can be counted; the note is optional, bounded and
    stripped of secrets and personal details, and is never used as an instruction. Never holds mail text."""

    __tablename__ = "shadow_feedback"
    __table_args__ = (
        Index("uq_shadow_feedback_target", "agent_id", "account_id", "thread_id", "kind", unique=True),
        Index("ix_shadow_feedback_agent_created_at", "agent_id", "created_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agent.id", name="fk_shadow_feedback_agent_id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    thread_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
