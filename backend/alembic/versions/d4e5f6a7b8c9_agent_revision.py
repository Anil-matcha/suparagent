"""Keep a history of an agent's profile edits.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""
from alembic import context, op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    inspector = None if context.is_offline_mode() else sa.inspect(op.get_bind())
    if inspector is not None and "agent_revision" in inspector.get_table_names():
        return
    op.create_table(
        "agent_revision",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.Identity(always=True), primary_key=True),
        sa.Column("agent_id", sa.BigInteger(), sa.ForeignKey("agent.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column("redacted", sa.JSON(), nullable=False),
        sa.Column("restored_from", sa.BigInteger(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_agent_revision_number", "agent_revision", ["agent_id", "number"], unique=True)


def downgrade():
    op.drop_index("uq_agent_revision_number", table_name="agent_revision")
    op.drop_table("agent_revision")
