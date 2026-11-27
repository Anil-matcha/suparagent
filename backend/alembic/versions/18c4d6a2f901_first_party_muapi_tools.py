"""First-party MuAPI API key and per-agent tool grants.

Revision ID: 18c4d6a2f901
Revises: a1f4d7c2e856, c7d4e2a91f60, a7c3e9d1f206, b6d1f4a9c3e7
"""
from alembic import op
import sqlalchemy as sa


revision = "18c4d6a2f901"
down_revision = ("a1f4d7c2e856", "c7d4e2a91f60", "a7c3e9d1f206", "b6d1f4a9c3e7")
branch_labels = None
depends_on = None


def upgrade():
    # Remove the retired MuAPI marketplace installation and its separate
    # encrypted MCP bearer token. The first-party REST key is configured anew.
    bind = op.get_bind()
    bind.execute(sa.text("""
        DELETE FROM community_mcp_tool_grant
        WHERE connection_id IN (
            SELECT id FROM community_mcp_connection
            WHERE owner_id = :owner_id AND slug = 'muapi'
        )
    """), {"owner_id": "local-owner"})
    bind.execute(sa.text("""
        DELETE FROM community_mcp_connection
        WHERE owner_id = :owner_id AND slug = 'muapi'
    """), {"owner_id": "local-owner"})
    bind.execute(sa.text("""
        DELETE FROM user_credential
        WHERE user_id = :owner_id AND provider = 'integration:community-mcp:muapi'
    """), {"owner_id": "local-owner"})
    op.create_table(
        "muapi_tool_grant",
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        sa.Column("schema_digest", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", "tool_name"),
    )
    op.create_index("ix_muapi_tool_grant_agent", "muapi_tool_grant", ["agent_id"])


def downgrade():
    op.drop_index("ix_muapi_tool_grant_agent", table_name="muapi_tool_grant")
    op.drop_table("muapi_tool_grant")
