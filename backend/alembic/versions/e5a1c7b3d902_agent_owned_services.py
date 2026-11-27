"""Agent-owned mailbox and phone-number resources and their agent assignment.

Revision ID: e5a1c7b3d902
Revises: d4b8e1f7a936
"""
from alembic import op
import sqlalchemy as sa

revision = "e5a1c7b3d902"
down_revision = "d4b8e1f7a936"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_service_provider",
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("resource_limit", sa.Integer(), server_default="3", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["custom_mcp_connection.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("connection_id"),
    )
    op.create_table(
        "agent_service_resource",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("address", sa.String(length=200), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("provider_resource_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["custom_mcp_connection.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "kind", "address", name="uq_agent_service_resource_address"),
    )
    op.create_index("ix_agent_service_resource_owner", "agent_service_resource", ["owner_id"])
    op.create_table(
        "agent_service_assignment",
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["agent_service_resource.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", "resource_id"),
    )
    op.create_index("ix_agent_service_assignment_resource", "agent_service_assignment", ["resource_id"])


def downgrade():
    op.drop_index("ix_agent_service_assignment_resource", table_name="agent_service_assignment")
    op.drop_table("agent_service_assignment")
    op.drop_index("ix_agent_service_resource_owner", table_name="agent_service_resource")
    op.drop_table("agent_service_resource")
    op.drop_table("agent_service_provider")
