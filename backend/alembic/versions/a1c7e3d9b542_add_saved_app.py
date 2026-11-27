"""Saved apps: a page kept as an app that outlives its chat; no existing row changes."""
from alembic import context, op
from app.db.types import JSONValue, UTCDateTime
import sqlalchemy as sa

revision = "a1c7e3d9b542"
down_revision = "e8f5b0d3c749"
branch_labels = None
depends_on = None


def upgrade():
    names = set() if context.is_offline_mode() else set(sa.inspect(op.get_bind()).get_table_names())
    if context.is_offline_mode() or "saved_app" not in names:
        op.create_table(
            "saved_app",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("agent_id", sa.BigInteger(), nullable=True),
            sa.Column("thread_id", sa.BigInteger(), nullable=True),
            sa.Column("page_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("pinned_rev", sa.Integer(), nullable=True),
            sa.Column("versions", JSONValue, nullable=False),
            sa.Column("uses", JSONValue, nullable=False),
            sa.Column("created_at", UTCDateTime, nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", UTCDateTime, nullable=False, server_default=sa.func.now()),
        )
        op.create_index("uq_saved_app_owner_page", "saved_app", ["owner_id", "page_id"], unique=True)


def downgrade():
    op.drop_table("saved_app")
