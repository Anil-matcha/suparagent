from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONValue, UTCDateTime


class SavedApp(Base):
    """A page saved as an app, so it outlives the chat that showed it.

    `thread_id` and `agent_id` are plain values with no foreign key: deleting the chat or the agent must not
    delete the app. `versions` holds each saved revision of the page, newest last (at most 20, plus the pinned
    one); it is replaced whole, never changed in place."""

    __tablename__ = "saved_app"
    __table_args__ = (
        Index("uq_saved_app_owner_page", "owner_id", "page_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    page_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    pinned_rev: Mapped[int | None] = mapped_column(Integer, nullable=True)
    versions: Mapped[list] = mapped_column(JSONValue, nullable=False, default=list)
    uses: Mapped[list] = mapped_column(JSONValue, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
