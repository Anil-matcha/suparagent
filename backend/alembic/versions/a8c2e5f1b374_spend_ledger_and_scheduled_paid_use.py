"""Spend ledger for paid marketplace calls and the per-agent scheduled paid use opt-in.

Revision ID: a8c2e5f1b374
Revises: d4b8e1f7a936
"""
from alembic import context, op
import sqlalchemy as sa

from app.db.types import BigIntPK, UTCDateTime

revision = "a8c2e5f1b374"
down_revision = "d4b8e1f7a936"
branch_labels = None
depends_on = None


def upgrade():
    offline = context.is_offline_mode()
    if offline or not sa.inspect(op.get_bind()).has_table("spend_ledger"):
        op.create_table(
            "spend_ledger",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("agent_id", sa.BigInteger(), nullable=False),
            sa.Column("run_key", sa.String(), nullable=False),
            sa.Column("routine_run_id", sa.BigInteger(), nullable=True),
            sa.Column("tool", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("cap_cents", sa.Integer(), nullable=False),
            sa.Column("settled_cents", sa.Integer(), nullable=True),
            sa.Column("reason", sa.String(), nullable=True),
            sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("settled_at", UTCDateTime(), nullable=True),
            sqlite_autoincrement=True,
        )
        op.create_index("ix_spend_ledger_agent_created", "spend_ledger", ["owner_id", "agent_id", "created_at"])
        op.create_index("ix_spend_ledger_run_key", "spend_ledger", ["run_key"])
    if offline or "scheduled_paid_use" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent")}:
        op.add_column("agent", sa.Column("scheduled_paid_use", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    op.drop_column("agent", "scheduled_paid_use")
    op.drop_index("ix_spend_ledger_run_key", table_name="spend_ledger")
    op.drop_index("ix_spend_ledger_agent_created", table_name="spend_ledger")
    op.drop_table("spend_ledger")
