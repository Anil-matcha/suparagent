"""Non-secret record of agent calls made through an agent-owned mailbox or number.

Revision ID: f6b2d8c4e013
Revises: e5a1c7b3d902
"""
from alembic import op
import sqlalchemy as sa

revision = "f6b2d8c4e013"
down_revision = "e5a1c7b3d902"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_service_audit",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_name", sa.String(length=100), server_default="", nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("connection_name", sa.String(length=120), server_default="", nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        sa.Column("resources", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("approved_by", sa.String(length=100), nullable=True),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("turn_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_service_audit_owner_created", "agent_service_audit", ["owner_id", "created_at"])
    op.create_index("ix_agent_service_audit_agent", "agent_service_audit", ["agent_id"])


def downgrade():
    op.drop_index("ix_agent_service_audit_agent", table_name="agent_service_audit")
    op.drop_index("ix_agent_service_audit_owner_created", table_name="agent_service_audit")
    op.drop_table("agent_service_audit")
