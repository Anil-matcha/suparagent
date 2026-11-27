from datetime import date, datetime

from sqlalchemy import BigInteger, Date, ForeignKey, Identity, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime

APP_EVENT_TRIGGER_STATES = ("active", "paused", "error", "removed")  # checked in the service


class AppEventTrigger(Base):
    """The Composio side of a routine's new-mail trigger (BL-177): one trigger instance in the owner's project."""

    __tablename__ = "app_event_trigger"
    __table_args__ = (
        Index("uq_app_event_trigger_routine", "routine_id", unique=True),  # one event trigger per routine in v1
        Index("uq_app_event_trigger_composio_id", "composio_trigger_id", unique=True),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    routine_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("routine.id", name="fk_app_event_trigger_routine_id", ondelete="CASCADE"), nullable=False
    )
    composio_trigger_id: Mapped[str] = mapped_column(String, nullable=False)
    composio_account_id: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    query: Mapped[str] = mapped_column(String, nullable=False, default="", server_default="")
    polling_interval: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=False, default="active", server_default="active")
    last_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)


class AppEventReceipt(Base):
    """A bounded dedupe window: one row per event already accepted for a trigger (hash of the event id only)."""

    __tablename__ = "app_event_receipt"
    __table_args__ = (Index("ix_app_event_receipt_received_at", "received_at"),)

    trigger_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_event_trigger.id", name="fk_app_event_receipt_trigger_id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_hash: Mapped[str] = mapped_column(String, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)


class AppEventDay(Base):
    """Counters per trigger and UTC day. Counts only: never an id, a sender or any text."""

    __tablename__ = "app_event_day"

    trigger_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_event_trigger.id", name="fk_app_event_day_trigger_id", ondelete="CASCADE"),
        primary_key=True,
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    received: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    started: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    merged: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    outside_share: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    unrouted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    dropped_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    dropped_quiet: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
