"""add agent tool scope, tools_off, outbound_policy, reply_style, check_work, and cache_keep_warm

Revision ID: f9a6c1e4d850
Revises: e8f5b0d3c749
Create Date: 2026-11-27 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "f9a6c1e4d850"
down_revision: Union[str, Sequence[str], None] = "e8f5b0d3c749"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _agent_columns() -> set[str]:
    if context.is_offline_mode():
        return set()
    bind = op.get_bind()
    return {col["name"] for col in sa.inspect(bind).get_columns("agent")}


def upgrade() -> None:
    cols = _agent_columns()
    if "tool_scope" not in cols:
        op.add_column("agent", sa.Column("tool_scope", sa.JSON(), nullable=True))
    if "tools_off" not in cols:
        op.add_column("agent", sa.Column("tools_off", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    if "outbound_policy" not in cols:
        op.add_column("agent", sa.Column("outbound_policy", sa.String(length=16), nullable=False, server_default="ask"))
    if "reply_style" not in cols:
        op.add_column("agent", sa.Column("reply_style", sa.String(length=24), nullable=False, server_default="default"))
    if "check_work" not in cols:
        op.add_column("agent", sa.Column("check_work", sa.JSON(), nullable=True))
    if "cache_keep_warm" not in cols:
        op.add_column("agent", sa.Column("cache_keep_warm", sa.Boolean(), nullable=True))


def downgrade() -> None:
    cols = _agent_columns()
    for name in ("cache_keep_warm", "check_work", "reply_style", "outbound_policy", "tools_off", "tool_scope"):
        if name in cols or context.is_offline_mode():
            with op.batch_alter_table("agent") as batch_op:
                batch_op.drop_column(name)
