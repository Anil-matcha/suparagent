"""Browser sign-in (OAuth) for owner-added MCP servers.

Revision ID: b5e8f1a3c207
Revises: a9c2e4f6b813
"""
from alembic import op
import sqlalchemy as sa

revision = "b5e8f1a3c207"
down_revision = "a9c2e4f6b813"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_mcp_connection", sa.Column("oauth_config", sa.JSON(none_as_null=True), nullable=True))
    op.create_table(
        "custom_mcp_oauth_flow",
        sa.Column("state_digest", sa.String(length=64), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("connection_revision", sa.Integer(), nullable=False),
        sa.Column("verifier_cipher", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.String(length=500), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["custom_mcp_connection.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("state_digest"),
    )
    op.create_index("ix_custom_mcp_oauth_flow_connection", "custom_mcp_oauth_flow", ["connection_id"])


def downgrade():
    op.drop_index("ix_custom_mcp_oauth_flow_connection", table_name="custom_mcp_oauth_flow")
    op.drop_table("custom_mcp_oauth_flow")
    op.drop_column("custom_mcp_connection", "oauth_config")
