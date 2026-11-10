"""Human members of one workspace: who they are, how they joined, what changed.

A workspace is one install (or one hosted sandbox). The owner is the person
who set it up; others join by invitation. Secrets are never stored: passwords
as a slow KDF hash, invitation tokens as SHA-256 digests of high-entropy values.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Identity, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime

ROLES = ("owner", "admin", "member", "viewer")
INVITE_ROLES = ("admin", "member", "viewer")


class Person(Base):
    """A human who can sign in to this workspace. Exactly one active owner."""

    __tablename__ = "person"
    __table_args__ = (
        Index("uq_person_email", "email", unique=True),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    # Lower-cased. Null only for the owner of an install that never gave one.
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    # active | removed
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class PersonInvite(Base):
    """A one-time, email-bound invitation. Single use, expiring, revocable."""

    __tablename__ = "person_invite"
    __table_args__ = (
        Index("uq_person_invite_token_hash", "token_hash", unique=True),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    invited_by_person_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    invited_by_label: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class PeopleAudit(Base):
    """Membership history: invites, joins, role changes and removals."""

    __tablename__ = "people_audit"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    actor_person_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actor_label: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    subject_person_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    subject_label: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    detail: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False, index=True)
