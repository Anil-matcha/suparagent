"""What a support worker would do with a mail thread in shadow mode (BL-173); no existing row changes."""
from alembic import context, op
from app.db.types import BigIntPK, UTCDateTime
import sqlalchemy as sa

revision = "b5c2e7a914d3"
down_revision = "a4e1c7d93b25"
branch_labels = None
depends_on = None


def upgrade():
    if context.is_offline_mode() or "shadow_prediction" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "shadow_prediction",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("agent_id", sa.BigInteger(), sa.ForeignKey("agent.id", name="fk_shadow_prediction_agent_id", ondelete="CASCADE"), nullable=False),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("thread_id", sa.String(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(), nullable=False),
            sa.Column("owner_only", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("draft_ref", sa.String(), nullable=True),
            sa.Column("draft_digest", sa.String(), nullable=True),
            sa.Column("draft_text", sa.Text(), nullable=True),
            sa.Column("turn_id", sa.BigInteger(), nullable=True),
            sa.Column("predicted_at", UTCDateTime, nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )
        op.create_index("uq_shadow_prediction_mark", "shadow_prediction", ["agent_id", "account_id", "thread_id", "turn_id"], unique=True)
        op.create_index("ix_shadow_prediction_agent_predicted_at", "shadow_prediction", ["agent_id", "predicted_at"])


def downgrade():
    op.drop_table("shadow_prediction")
