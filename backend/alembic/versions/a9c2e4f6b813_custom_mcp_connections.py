"""Owner-entered remote MCP connections and their exact agent tool grants.

Revision ID: a9c2e4f6b813
Revises: e3f4a5b6c7d8
"""
from alembic import op
import sqlalchemy as sa

revision = "a9c2e4f6b813"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "custom_mcp_connection",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("transport", sa.String(length=32), server_default="streamable_http", nullable=False),
        sa.Column("endpoint", sa.String(length=300), nullable=False),
        sa.Column("auth_mode", sa.String(length=16), server_default="none", nullable=False),
        sa.Column("auth_header_name", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("tested_revision", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("tools_snapshot", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("tools_digest", sa.String(length=64), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_custom_mcp_connection_owner", "custom_mcp_connection", ["owner_id"])
    op.create_table(
        "custom_mcp_tool_grant",
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        sa.Column("schema_digest", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("input_schema", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["custom_mcp_connection.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", "connection_id", "tool_name"),
    )
    op.create_index("ix_custom_mcp_tool_connection", "custom_mcp_tool_grant", ["connection_id"])


def downgrade():
    op.drop_index("ix_custom_mcp_tool_connection", table_name="custom_mcp_tool_grant")
    op.drop_table("custom_mcp_tool_grant")
    op.drop_index("ix_custom_mcp_connection_owner", table_name="custom_mcp_connection")
    op.drop_table("custom_mcp_connection")
