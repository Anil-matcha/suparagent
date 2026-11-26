"""Add the share of a mailbox an agent may see.

Revision ID: a4e1c7d93b25
Revises: 18c4d6a2f901
"""
from alembic import context, op
import sqlalchemy as sa

revision = "a4e1c7d93b25"
down_revision = "18c4d6a2f901"
branch_labels = None
depends_on = None


def upgrade():
    if context.is_offline_mode() or "connector_shares" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent")
    }:
        op.add_column(
            "agent",
            sa.Column(
                "connector_shares",
                sa.JSON(none_as_null=True),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )


def downgrade():
    op.drop_column("agent", "connector_shares")
