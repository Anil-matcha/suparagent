"""People, invitations and a membership audit; attribute sessions and turns to a person.

Revision ID: c9d8e7f6a5b4
Revises: e5f6a7b8c9d0
"""
from alembic import context, op
import sqlalchemy as sa

revision = "c9d8e7f6a5b4"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def _pk():
    return sa.Column(
        "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.Identity(always=True), primary_key=True
    )


def _created():
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _existing():
    if context.is_offline_mode():
        return set(), lambda table: set()
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names()), lambda table: {c["name"] for c in inspector.get_columns(table)}


def upgrade():
    tables, columns = _existing()
    if "person" not in tables:
        op.create_table(
            "person",
            _pk(),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("display_name", sa.String(), nullable=False),
            sa.Column("password_hash", sa.String(), nullable=True),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
            _created(),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
            sqlite_autoincrement=True,
        )
        op.create_index("uq_person_email", "person", ["email"], unique=True)
    if "person_invite" not in tables:
        op.create_table(
            "person_invite",
            _pk(),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("invited_by_person_id", sa.BigInteger(), nullable=True),
            sa.Column("invited_by_label", sa.String(), nullable=False, server_default=""),
            _created(),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sqlite_autoincrement=True,
        )
        op.create_index("uq_person_invite_token_hash", "person_invite", ["token_hash"], unique=True)
        op.create_index("ix_person_invite_email", "person_invite", ["email"])
    if "people_audit" not in tables:
        op.create_table(
            "people_audit",
            _pk(),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("actor_person_id", sa.BigInteger(), nullable=True),
            sa.Column("actor_label", sa.String(), nullable=False, server_default=""),
            sa.Column("subject_person_id", sa.BigInteger(), nullable=True),
            sa.Column("subject_label", sa.String(), nullable=False, server_default=""),
            sa.Column("detail", sa.String(), nullable=False, server_default=""),
            _created(),
            sqlite_autoincrement=True,
        )
        op.create_index("ix_people_audit_created_at", "people_audit", ["created_at"])
    if "person_id" not in columns("auth_session"):
        op.add_column("auth_session", sa.Column("person_id", sa.BigInteger(), nullable=True))
        op.create_index("ix_auth_session_person_id", "auth_session", ["person_id"])
    turn_columns = columns("turn")
    if "person_id" not in turn_columns:
        op.add_column("turn", sa.Column("person_id", sa.BigInteger(), nullable=True))
    if "person_label" not in turn_columns:
        op.add_column("turn", sa.Column("person_label", sa.String(), nullable=True))


def downgrade():
    op.drop_column("turn", "person_label")
    op.drop_column("turn", "person_id")
    op.drop_index("ix_auth_session_person_id", table_name="auth_session")
    op.drop_column("auth_session", "person_id")
    op.drop_table("people_audit")
    op.drop_table("person_invite")
    op.drop_table("person")
