"""Member permissions, and who answered an approval.

Revision ID: d2e3f4a5b6c7
Revises: c9d8e7f6a5b4
"""
from alembic import context, op
import sqlalchemy as sa

revision = "d2e3f4a5b6c7"
down_revision = "c9d8e7f6a5b4"
branch_labels = None
depends_on = None


def _columns(table):
    if context.is_offline_mode():
        return set()
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "permissions" not in _columns("person"):
        op.add_column("person", sa.Column("permissions", sa.String(), nullable=True))
    if "permissions" not in _columns("person_invite"):
        op.add_column("person_invite", sa.Column("permissions", sa.String(), nullable=True))
    approval = _columns("approval")
    if "decided_by_person_id" not in approval:
        op.add_column("approval", sa.Column("decided_by_person_id", sa.BigInteger(), nullable=True))
    if "decided_by_label" not in approval:
        op.add_column("approval", sa.Column("decided_by_label", sa.String(), nullable=True))


def downgrade():
    op.drop_column("approval", "decided_by_label")
    op.drop_column("approval", "decided_by_person_id")
    op.drop_column("person_invite", "permissions")
    op.drop_column("person", "permissions")
