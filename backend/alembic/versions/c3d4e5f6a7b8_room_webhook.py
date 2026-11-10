"""Let a room accept posts by webhook.

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
"""
from alembic import context, op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def _inspector():
    return None if context.is_offline_mode() else sa.inspect(op.get_bind())


def upgrade():
    inspector = _inspector()
    if inspector is None or "webhook_secret_hash" not in {c["name"] for c in inspector.get_columns("room")}:
        op.add_column("room", sa.Column("webhook_secret_hash", sa.String(), nullable=True))
    if inspector is None or "room_webhook_receipt" not in inspector.get_table_names():
        op.create_table(
            "room_webhook_receipt",
            sa.Column("room_id", sa.BigInteger(), sa.ForeignKey("room.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("key_hash", sa.String(64), primary_key=True),
            sa.Column("payload_hash", sa.String(64), nullable=False),
            sa.Column("seq", sa.BigInteger(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_room_webhook_receipt_expires_at", "room_webhook_receipt", ["expires_at"])


def downgrade():
    op.drop_index("ix_room_webhook_receipt_expires_at", table_name="room_webhook_receipt")
    op.drop_table("room_webhook_receipt")
    op.drop_column("room", "webhook_secret_hash")
