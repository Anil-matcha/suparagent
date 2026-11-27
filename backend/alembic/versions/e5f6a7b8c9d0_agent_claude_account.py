"""Let an agent pick a named Claude account.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""
from alembic import context, op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    if not context.is_offline_mode():
        columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent")}
        if "claude_account" in columns:
            return
    op.add_column("agent", sa.Column("claude_account", sa.String(), nullable=True))


def downgrade():
    op.drop_column("agent", "claude_account")
