"""Labels the support worker may apply under Worker/ and a record of each one applied (BL-173); no existing row changes."""
from alembic import context, op
from app.db.types import BigIntPK, UTCDateTime
import sqlalchemy as sa

revision = "c6d3f8b1a527"
down_revision = "b5c2e7a914d3"
branch_labels = None
depends_on = None


def upgrade():
    names = set() if context.is_offline_mode() else set(sa.inspect(op.get_bind()).get_table_names())
    if context.is_offline_mode() or "worker_label" not in names:
        op.create_table(
            "worker_label",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("gmail_label_id", sa.String(), nullable=False),
            sa.Column("created_at", UTCDateTime, nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )
        op.create_index("uq_worker_label_account_name", "worker_label", ["account_id", "name"], unique=True)
    if context.is_offline_mode() or "worker_label_action" not in names:
        op.create_table(
            "worker_label_action",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("agent_id", sa.BigInteger(), sa.ForeignKey("agent.id", name="fk_worker_label_action_agent_id", ondelete="CASCADE"), nullable=False),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("thread_id", sa.String(), nullable=False),
            sa.Column("label_name", sa.String(), nullable=False),
            sa.Column("gmail_label_id", sa.String(), nullable=False),
            sa.Column("turn_id", sa.BigInteger(), nullable=True),
            sa.Column("run_id", sa.BigInteger(), nullable=True),
            sa.Column("request_key", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("code", sa.String(), nullable=True),
            sa.Column("applied_at", UTCDateTime, nullable=False, server_default=sa.func.now()),
            sa.Column("undone_at", UTCDateTime, nullable=True),
            sqlite_autoincrement=True,
        )
        op.create_index("ix_worker_label_action_agent_applied_at", "worker_label_action", ["agent_id", "applied_at"])
        op.create_index("ix_worker_label_action_account_thread", "worker_label_action", ["account_id", "thread_id"])


def downgrade():
    op.drop_table("worker_label_action")
    op.drop_table("worker_label")
