from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONValue, UTCDateTime


class MuapiToolGrant(Base):
    """Exact first-party MuAPI tools enabled for one agent."""

    __tablename__ = "muapi_tool_grant"
    __table_args__ = (Index("ix_muapi_tool_grant_agent", "agent_id"),)

    agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agent.id", ondelete="CASCADE"), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    schema_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSONValue, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
