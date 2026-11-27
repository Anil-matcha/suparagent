from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Identity, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime


class ShadowPrediction(Base):
    """What a support worker would do with one mail thread while it works in shadow mode (BL-173).

    Written by the `gmail_triage_mark` tool and nothing else; it never touches Gmail. The comparison
    report (services/shadow_report.py) later sets these beside what the owner actually did. One row
    per agent, account, thread and turn, so a repeated mark in the same turn replaces itself, while a
    later run's view of the same thread is a new row. `draft_text` is bounded, has secrets and personal
    details stripped, and is deleted after the retention window."""

    __tablename__ = "shadow_prediction"
    __table_args__ = (
        Index("uq_shadow_prediction_mark", "agent_id", "account_id", "thread_id", "turn_id", unique=True),
        Index("ix_shadow_prediction_agent_predicted_at", "agent_id", "predicted_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agent.id", name="fk_shadow_prediction_agent_id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    thread_id: Mapped[str] = mapped_column(String, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    owner_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=expression.false())
    draft_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    draft_digest: Mapped[str | None] = mapped_column(String, nullable=True)
    draft_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Null for a mark made outside a turn (none today); no foreign key so a pruned turn never deletes evidence.
    turn_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    predicted_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
