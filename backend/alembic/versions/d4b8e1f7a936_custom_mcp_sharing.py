"""Who added a custom MCP connection and whether the workspace shares it.

Revision ID: d4b8e1f7a936
Revises: c3a7d9e1b542
"""
from alembic import op
import sqlalchemy as sa

revision = "d4b8e1f7a936"
down_revision = "c3a7d9e1b542"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_mcp_connection", sa.Column("owner_person_id", sa.BigInteger(), nullable=True))
    op.add_column("custom_mcp_connection", sa.Column("shared", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("custom_mcp_oauth_flow", sa.Column("person_id", sa.BigInteger(), nullable=True))


def downgrade():
    op.drop_column("custom_mcp_oauth_flow", "person_id")
    op.drop_column("custom_mcp_connection", "shared")
    op.drop_column("custom_mcp_connection", "owner_person_id")
