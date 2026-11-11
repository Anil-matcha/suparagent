"""Hosted workspaces: the control plane's ID for a person.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""
from alembic import context, op
import sqlalchemy as sa

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def _has_column() -> bool:
    if context.is_offline_mode():
        return False
    return "external_id" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("person")}


def upgrade():
    if not _has_column():
        op.add_column("person", sa.Column("external_id", sa.String(), nullable=True))
        op.create_index("uq_person_external_id", "person", ["external_id"], unique=True)


def downgrade():
    op.drop_index("uq_person_external_id", table_name="person")
    op.drop_column("person", "external_id")
