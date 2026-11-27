"""What a custom MCP server called itself at its last successful test.

Revision ID: c3a7d9e1b542
Revises: b5e8f1a3c207
"""
from alembic import op
import sqlalchemy as sa

revision = "c3a7d9e1b542"
down_revision = "b5e8f1a3c207"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_mcp_connection", sa.Column("server_name", sa.String(length=100), nullable=True))
    op.add_column("custom_mcp_connection", sa.Column("server_version", sa.String(length=100), nullable=True))


def downgrade():
    op.drop_column("custom_mcp_connection", "server_version")
    op.drop_column("custom_mcp_connection", "server_name")
