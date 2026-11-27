"""One row per paid marketplace call: what was reserved, what it settled at, or why it was refused."""
from datetime import datetime

from sqlalchemy import BigInteger, Identity, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime

# reserved: counted at the call's cap until the result arrives; settled: counted at the quoted cost;
# released: the call never went out; refused: a limit stopped it and nothing was reserved.
SPEND_STATUSES = ("reserved", "settled", "released", "refused")


class SpendLedger(Base):
    __tablename__ = "spend_ledger"
    __table_args__ = (
        Index("ix_spend_ledger_agent_created", "owner_id", "agent_id", "created_at"),
        Index("ix_spend_ledger_run_key", "run_key"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    # No foreign keys: the history outlives a deleted agent, run or conversation.
    agent_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # "routine:<run id>" for a scheduled run, "turn:<turn id>" for a chat turn.
    run_key: Mapped[str] = mapped_column(String, nullable=False)
    routine_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tool: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    cap_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    settled_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # per_call, per_run or monthly for a refusal.
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
