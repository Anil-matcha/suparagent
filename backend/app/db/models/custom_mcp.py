from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONValue, UTCDateTime


class CustomMcpConnection(Base):
    """An owner-entered remote MCP server. Secrets live in the encrypted credential store."""

    __tablename__ = "custom_mcp_connection"
    __table_args__ = (Index("ix_custom_mcp_connection_owner", "owner_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False, default="streamable_http", server_default="streamable_http")
    endpoint: Mapped[str] = mapped_column(String(300), nullable=False)
    auth_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="none", server_default="none")
    auth_header_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Who added it (a signed-in member's person id; null for the workspace owner, the desktop app or a paired
    # device) and whether the whole workspace may use it. A connection starts private to its creator.
    owner_person_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    tested_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # What the server called itself at the last successful test. Display only.
    server_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    server_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Non-secret OAuth settings (endpoints, client id, scopes). Tokens and client secrets are never here.
    oauth_config: Mapped[dict | None] = mapped_column(JSONValue, nullable=True)
    tools_snapshot: Mapped[list | None] = mapped_column(JSONValue, nullable=True)
    tools_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class CustomMcpToolGrant(Base):
    """Exact per-agent tool grants, bound to the schema digest shown when granted."""

    __tablename__ = "custom_mcp_tool_grant"
    __table_args__ = (Index("ix_custom_mcp_tool_connection", "connection_id"),)

    agent_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agent.id", ondelete="CASCADE"), primary_key=True
    )
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("custom_mcp_connection.id", ondelete="CASCADE"), primary_key=True
    )
    tool_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    schema_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    input_schema: Mapped[dict] = mapped_column(JSONValue, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)


class CustomMcpOAuthFlow(Base):
    """One pending browser sign-in. The provider's `state` is stored only as a digest."""

    __tablename__ = "custom_mcp_oauth_flow"
    __table_args__ = (Index("ix_custom_mcp_oauth_flow_connection", "connection_id"),)

    state_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("custom_mcp_connection.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    person_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    connection_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    verifier_cipher: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
