"""The owner's feedback on what the support worker predicted in shadow mode (BL-175); no existing row changes."""
from alembic import context, op
from app.db.types import BigIntPK, UTCDateTime
import sqlalchemy as sa

revision = "e8f5b0d3c749"
down_revision = "d7e4a9c2b638"
branch_labels = None
depends_on = None


def upgrade():
    if context.is_offline_mode() or "shadow_feedback" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "shadow_feedback",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("agent_id", sa.BigInteger(), sa.ForeignKey("agent.id", name="fk_shadow_feedback_agent_id", ondelete="CASCADE"), nullable=False),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("thread_id", sa.String(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("reason", sa.String(), nullable=False),
            sa.Column("note", sa.String(), nullable=True),
            sa.Column("created_at", UTCDateTime, nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )
        op.create_index("uq_shadow_feedback_target", "shadow_feedback", ["agent_id", "account_id", "thread_id", "kind"], unique=True)
        op.create_index("ix_shadow_feedback_agent_created_at", "shadow_feedback", ["agent_id", "created_at"])


def downgrade():
    op.drop_table("shadow_feedback")
