"""A routine's optional new-mail trigger and the receipts and counters behind it (BL-177); existing routines have none."""
from alembic import context, op
from app.db.types import UTCDateTime
import sqlalchemy as sa

revision = "d7e4a9c2b638"
down_revision = "c6d3f8b1a527"
branch_labels = None
depends_on = None

COUNTERS = ("received", "started", "merged", "duplicate", "outside_share", "rejected", "unrouted", "dropped_cap", "dropped_quiet")


def upgrade():
    inspector = None if context.is_offline_mode() else sa.inspect(op.get_bind())
    if inspector is None or "app_event" not in {c["name"] for c in inspector.get_columns("routine")}:
        op.add_column("routine", sa.Column("app_event", sa.JSON(), nullable=True))
    if inspector is None or "uq_routine_run_app_event_slot" not in {i["name"] for i in inspector.get_indexes("routine_run")}:
        op.create_index("uq_routine_run_app_event_slot", "routine_run", ["routine_id", "scheduled_for"], unique=True,
                        sqlite_where=sa.text("trigger = 'app_event'"), postgresql_where=sa.text("trigger = 'app_event'"))
    names = set() if inspector is None else set(inspector.get_table_names())
    if inspector is None or "app_event_trigger" not in names:
        from app.db.types import BigIntPK
        op.create_table(
            "app_event_trigger",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("routine_id", sa.BigInteger(), sa.ForeignKey("routine.id", name="fk_app_event_trigger_routine_id", ondelete="CASCADE"), nullable=False),
            sa.Column("composio_trigger_id", sa.String(), nullable=False),
            sa.Column("composio_account_id", sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("query", sa.String(), nullable=False, server_default=""),
            sa.Column("polling_interval", sa.Integer(), nullable=True),
            sa.Column("state", sa.String(), nullable=False, server_default="active"),
            sa.Column("last_event_at", UTCDateTime, nullable=True),
            sa.Column("last_error", sa.String(), nullable=True),
            sa.Column("created_at", UTCDateTime, nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )
        op.create_index("uq_app_event_trigger_routine", "app_event_trigger", ["routine_id"], unique=True)
        op.create_index("uq_app_event_trigger_composio_id", "app_event_trigger", ["composio_trigger_id"], unique=True)
    if inspector is None or "app_event_receipt" not in names:
        op.create_table(
            "app_event_receipt",
            sa.Column("trigger_id", sa.BigInteger(), sa.ForeignKey("app_event_trigger.id", name="fk_app_event_receipt_trigger_id", ondelete="CASCADE"), primary_key=True),
            sa.Column("event_hash", sa.String(), primary_key=True),
            sa.Column("received_at", UTCDateTime, nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_app_event_receipt_received_at", "app_event_receipt", ["received_at"])
    if inspector is None or "app_event_day" not in names:
        op.create_table(
            "app_event_day",
            sa.Column("trigger_id", sa.BigInteger(), sa.ForeignKey("app_event_trigger.id", name="fk_app_event_day_trigger_id", ondelete="CASCADE"), primary_key=True),
            sa.Column("day", sa.Date(), primary_key=True),
            *[sa.Column(name, sa.Integer(), nullable=False, server_default="0") for name in COUNTERS],
        )


def downgrade():
    op.drop_table("app_event_day")
    op.drop_table("app_event_receipt")
    op.drop_table("app_event_trigger")
    op.drop_index("uq_routine_run_app_event_slot", table_name="routine_run")
    op.drop_column("routine", "app_event")
