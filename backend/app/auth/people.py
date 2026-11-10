"""Human members of a workspace: roles, invitations, sign-in and the membership audit.

Roles are layered on the scopes the auth middleware already understands, so a route
added later stays admin-only until someone lists it in policy.py:

- owner, admin: every scope (admin).
- member: chat, files and manage; never admin, so their posts stay "restricted"
  and agent configuration is refused, like any other non-admin sign-in.
- viewer: chat only, and the middleware refuses every write (`Principal.read_only`).

A person's scopes come from their *current* role on every request, never from the
session row, so a role change or a removal applies on the next request.
"""

import asyncio
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import credentials, live
from app.db.models.auth import AuthSession
from app.db.models.people import INVITE_ROLES, ROLES, PeopleAudit, Person, PersonInvite
from app.db.transactions import begin_write

INVITE_TTL = timedelta(days=7)
MAX_OPEN_INVITES = 50
MAX_PEOPLE = 200
NAME_MAX = 80
EMAIL_MAX = 254

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ROLE_SCOPES: dict[str, tuple[str, ...]] = {
    "owner": ("admin", "chat", "files"),
    "admin": ("admin", "chat", "files"),
    "member": ("chat", "files", "manage"),
    "viewer": ("chat",),
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def scopes_for_role(role: str) -> tuple[str, ...]:
    """Unknown roles get nothing useful: fail closed."""
    return ROLE_SCOPES.get(role, ())


class PeopleError(Exception):
    """A refusal with a stable code, mapped to an HTTP error by the routes."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class InviteInvalid(PeopleError):
    """Expired, revoked, used, unknown, or for another email: all look the same."""

    def __init__(self) -> None:
        super().__init__(
            400, "invite_invalid",
            "That invitation is not valid. It may have expired, been used or revoked, or be for another email.",
        )


@dataclass(frozen=True)
class Actor:
    """Who is making a change, for authorization and the audit trail."""

    role: str  # owner | admin | member | viewer
    person_id: int | None
    label: str


def clean_name(raw: str | None, fallback: str = "") -> str:
    text = "".join(ch if ch.isprintable() else " " for ch in (raw or "")).strip()
    return (" ".join(text.split()) or fallback)[:NAME_MAX]


def normalize_email(raw: str | None) -> str:
    email = (raw or "").strip().lower()
    if len(email) > EMAIL_MAX or not _EMAIL.match(email):
        raise PeopleError(422, "email_invalid", "Enter a valid email address.")
    return email


# --- reading ----------------------------------------------------------------


async def get_person(db: AsyncSession, person_id: int) -> Person | None:
    return (await db.execute(select(Person).where(Person.id == person_id))).scalar_one_or_none()


async def get_active_person(db: AsyncSession, person_id: int) -> Person | None:
    person = await get_person(db, person_id)
    return person if person is not None and person.status == "active" and person.role in ROLES else None


async def list_people(db: AsyncSession, *, include_removed: bool = False) -> list[Person]:
    query = select(Person).order_by(Person.created_at, Person.id)
    if not include_removed:
        query = query.where(Person.status == "active")
    return list((await db.execute(query)).scalars())


async def get_owner_person(db: AsyncSession) -> Person | None:
    return (
        await db.execute(select(Person).where(Person.role == "owner", Person.status == "active").limit(1))
    ).scalar_one_or_none()


async def ensure_owner_person(db: AsyncSession) -> Person:
    """The owner as a person, made on first need: installs that predate people have none."""
    owner = await get_owner_person(db)
    if owner is not None:
        return owner
    await begin_write(db)
    owner = await get_owner_person(db)
    if owner is None:
        owner = Person(display_name="Owner", role="owner", status="active")
        db.add(owner)
        await db.flush()
    await db.commit()
    return owner


# --- audit ------------------------------------------------------------------


def record(
    db: AsyncSession, action: str, actor: Actor | None, *,
    subject: Person | None = None, subject_label: str = "", detail: str = "",
) -> None:
    """Queue an audit row. Committed with the change it describes."""
    db.add(PeopleAudit(
        action=action,
        actor_person_id=actor.person_id if actor else None,
        actor_label=actor.label if actor else "",
        subject_person_id=subject.id if subject else None,
        subject_label=subject_label or (subject.email or subject.display_name if subject else ""),
        detail=detail[:500],
        created_at=utcnow(),
    ))


async def list_audit(db: AsyncSession, limit: int = 100) -> list[PeopleAudit]:
    result = await db.execute(
        select(PeopleAudit).order_by(PeopleAudit.created_at.desc(), PeopleAudit.id.desc()).limit(limit)
    )
    return list(result.scalars())


# --- invitations ------------------------------------------------------------


def _open_invites():
    return (PersonInvite.used_at.is_(None), PersonInvite.revoked_at.is_(None), PersonInvite.expires_at > utcnow())


async def list_open_invites(db: AsyncSession) -> list[PersonInvite]:
    result = await db.execute(
        select(PersonInvite).where(*_open_invites()).order_by(PersonInvite.created_at.desc(), PersonInvite.id.desc())
    )
    return list(result.scalars())


def _require_can_grant(actor: Actor, role: str) -> None:
    if role not in INVITE_ROLES:
        raise PeopleError(422, "role_invalid", "Choose admin, member or viewer.")
    if role == "admin" and actor.role != "owner":
        raise PeopleError(403, "owner_only", "Only the owner can make someone an admin.")


async def create_invite(db: AsyncSession, actor: Actor, *, email: str, role: str) -> tuple[str, PersonInvite]:
    """Returns the raw token once: only its digest is stored. Replaces any open
    invitation for the same email, so one address has at most one live link."""
    email = normalize_email(email)
    _require_can_grant(actor, role)
    await begin_write(db)
    existing = (await db.execute(select(Person).where(Person.email == email))).scalar_one_or_none()
    if existing is not None and existing.status == "active":
        await db.rollback()
        raise PeopleError(409, "already_member", "That person is already a member.")
    total = (await db.execute(
        select(func.count()).select_from(Person).where(Person.status == "active")
    )).scalar_one()
    open_count = (await db.execute(
        select(func.count()).select_from(PersonInvite).where(*_open_invites())
    )).scalar_one()
    if total + open_count >= MAX_PEOPLE or open_count >= MAX_OPEN_INVITES:
        await db.rollback()
        raise PeopleError(409, "too_many_invites", "Too many open invitations. Revoke one first.")
    now = utcnow()
    await db.execute(
        update(PersonInvite)
        .where(PersonInvite.email == email, *_open_invites())
        .values(revoked_at=now)
    )
    token = secrets.token_urlsafe(32)
    invite = PersonInvite(
        token_hash=credentials.digest(token), email=email, role=role,
        invited_by_person_id=actor.person_id, invited_by_label=actor.label,
        created_at=now, expires_at=now + INVITE_TTL,
    )
    db.add(invite)
    record(db, "invite.created", actor, subject_label=email, detail=f"role={role}")
    await db.commit()
    await db.refresh(invite)
    return token, invite


async def revoke_invite(db: AsyncSession, actor: Actor, invite_id: int) -> bool:
    await begin_write(db)
    invite = (await db.execute(
        select(PersonInvite).where(PersonInvite.id == invite_id, *_open_invites())
    )).scalar_one_or_none()
    if invite is None:
        await db.rollback()
        return False
    if invite.role == "admin" and actor.role != "owner":
        await db.rollback()
        raise PeopleError(403, "owner_only", "Only the owner can revoke an admin invitation.")
    invite.revoked_at = utcnow()
    record(db, "invite.revoked", actor, subject_label=invite.email, detail=f"role={invite.role}")
    await db.commit()
    return True


async def accept_invite(
    db: AsyncSession, *, token: str, email: str, display_name: str, password: str
) -> Person:
    """Consume an invitation and create (or re-activate) the person. Single use: of two
    concurrent acceptances only one consumes the row. A wrong email does not consume it."""
    try:
        email = normalize_email(email)
    except PeopleError:
        raise InviteInvalid() from None
    name = clean_name(display_name)
    if not name:
        raise PeopleError(422, "name_required", "Enter your name.")
    try:
        credentials.check_passphrase_strength(password)
    except credentials.WeakPassphrase as exc:
        raise PeopleError(422, "weak_password", str(exc)) from exc
    if not token or len(token) > 200:
        raise InviteInvalid()
    hashed_password = await asyncio.to_thread(credentials.hash_passphrase, password)
    token_hash = credentials.digest(token)
    now = utcnow()
    await begin_write(db)
    consumed = await db.execute(
        update(PersonInvite)
        .where(PersonInvite.token_hash == token_hash, PersonInvite.email == email, *_open_invites())
        .values(used_at=now)
    )
    if (consumed.rowcount or 0) != 1:
        await db.rollback()
        raise InviteInvalid()
    invite = (await db.execute(select(PersonInvite).where(PersonInvite.token_hash == token_hash))).scalar_one()
    person = (await db.execute(select(Person).where(Person.email == email))).scalar_one_or_none()
    if person is not None and person.status == "active":
        await db.rollback()
        raise PeopleError(409, "already_member", "That email already belongs to a member.")
    if person is None:
        person = Person(email=email, display_name=name, password_hash=hashed_password, role=invite.role,
                        status="active", created_at=now, updated_at=now)
        db.add(person)
    else:  # re-joining after removal: the row is kept so history still points at it
        person.display_name = name
        person.password_hash = hashed_password
        person.role = invite.role
        person.status = "active"
        person.removed_at = None
        person.updated_at = now
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise InviteInvalid() from None
    record(db, "member.joined", Actor(person.role, person.id, name), subject=person, detail=f"role={invite.role}")
    await db.commit()
    return person


# --- sign-in ----------------------------------------------------------------

_dummy_hash: str | None = None


async def _burn_time(password: str) -> None:
    """Spend the same scrypt time for an unknown email as for a known one."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = await asyncio.to_thread(credentials.hash_passphrase, secrets.token_urlsafe(16))
    await asyncio.to_thread(credentials.verify_passphrase, password, _dummy_hash)


async def authenticate_person(db: AsyncSession, email: str, password: str) -> Person | None:
    try:
        email = normalize_email(email)
    except PeopleError:
        await _burn_time(password)
        return None
    person = (await db.execute(select(Person).where(Person.email == email))).scalar_one_or_none()
    if person is None or person.status != "active" or not person.password_hash:
        await _burn_time(password)
        return None
    if not await asyncio.to_thread(credentials.verify_passphrase, password, person.password_hash):
        return None
    await begin_write(db)
    await db.execute(update(Person).where(Person.id == person.id).values(last_login_at=utcnow()))
    await db.commit()
    return person


# --- changing people --------------------------------------------------------


async def _live_session_ids(db: AsyncSession, person_id: int) -> list[int]:
    result = await db.execute(
        select(AuthSession.id).where(AuthSession.person_id == person_id, AuthSession.revoked_at.is_(None))
    )
    return list(result.scalars())


async def _revoke_sessions(db: AsyncSession, person_id: int) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.person_id == person_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


def _require_can_manage(actor: Actor, target: Person) -> None:
    if target.role == "owner":
        raise PeopleError(403, "owner_protected", "The owner cannot be changed or removed.")
    if target.role == "admin" and actor.role != "owner":
        raise PeopleError(403, "owner_only", "Only the owner can change or remove an admin.")


async def change_role(db: AsyncSession, actor: Actor, person_id: int, role: str) -> Person:
    await begin_write(db)
    person = await get_person(db, person_id)
    if person is None or person.status != "active":
        await db.rollback()
        raise PeopleError(404, "person_not_found", "No such member.")
    try:
        _require_can_manage(actor, person)
        _require_can_grant(actor, role)
    except PeopleError:
        await db.rollback()
        raise
    if actor.person_id is not None and actor.person_id == person.id:
        await db.rollback()
        raise PeopleError(403, "self_change", "You cannot change your own role.")
    if person.role != role:
        before = person.role
        person.role = role
        person.updated_at = utcnow()
        record(db, "member.role_changed", actor, subject=person, detail=f"{before} -> {role}")
    await db.commit()
    return person


async def remove_person(db: AsyncSession, actor: Actor, person_id: int) -> None:
    """Soft-remove: the row stays so past work still names them, but they cannot sign in and
    every session they have is revoked in the same transaction."""
    await begin_write(db)
    person = await get_person(db, person_id)
    if person is None or person.status != "active":
        await db.rollback()
        raise PeopleError(404, "person_not_found", "No such member.")
    try:
        _require_can_manage(actor, person)
    except PeopleError:
        await db.rollback()
        raise
    if actor.person_id is not None and actor.person_id == person.id:
        await db.rollback()
        raise PeopleError(403, "self_change", "You cannot remove yourself.")
    now = utcnow()
    person.status = "removed"
    person.removed_at = now
    person.updated_at = now
    person.password_hash = None
    session_ids = await _live_session_ids(db, person.id)
    await _revoke_sessions(db, person.id)
    await db.execute(
        update(PersonInvite)
        .where(PersonInvite.email == person.email, *_open_invites())
        .values(revoked_at=now)
    )
    record(db, "member.removed", actor, subject=person, detail=f"role={person.role}")
    await db.commit()
    live.close_sessions(session_ids)  # their open event streams end now, not at the next reconnect


async def rename_person(db: AsyncSession, person_id: int, display_name: str) -> Person:
    name = clean_name(display_name)
    if not name:
        raise PeopleError(422, "name_required", "Enter a name.")
    await begin_write(db)
    person = await get_person(db, person_id)
    if person is None or person.status != "active":
        await db.rollback()
        raise PeopleError(404, "person_not_found", "No such member.")
    person.display_name = name
    person.updated_at = utcnow()
    await db.commit()
    return person


# --- own credentials --------------------------------------------------------


async def change_password(
    db: AsyncSession, person_id: int, *, new_password: str, current_password: str | None = None,
    email: str | None = None, root_verified: bool = False, keep_session_id: int | None = None,
) -> Person:
    """Set a person's own password. Everyone who has one proves it with `current_password`.
    The owner of an install that predates people signs in with the root passphrase and has no
    email or password yet; they set both once, after the route checked the passphrase
    (`root_verified`). Every other session of the person is signed out."""
    try:
        credentials.check_passphrase_strength(new_password)
    except credentials.WeakPassphrase as exc:
        raise PeopleError(422, "weak_password", str(exc)) from exc
    person = await get_active_person(db, person_id)
    if person is None:
        raise PeopleError(404, "person_not_found", "No such member.")
    new_email = None
    if person.password_hash:
        if not current_password or not await asyncio.to_thread(
            credentials.verify_passphrase, current_password, person.password_hash
        ):
            raise PeopleError(401, "wrong_password", "The current password is not right.")
    else:
        if person.role != "owner" or not root_verified:
            raise PeopleError(403, "passphrase_required", "Confirm with the owner passphrase.")
        new_email = normalize_email(email)
    hashed = await asyncio.to_thread(credentials.hash_passphrase, new_password)
    await begin_write(db)
    person = await get_active_person(db, person_id)
    if person is None:
        await db.rollback()
        raise PeopleError(404, "person_not_found", "No such member.")
    if new_email is not None:
        taken = (await db.execute(
            select(Person.id).where(Person.email == new_email, Person.id != person.id)
        )).first()
        if taken is not None:
            await db.rollback()
            raise PeopleError(409, "email_taken", "That email already belongs to someone else.")
        person.email = new_email
    person.password_hash = hashed
    person.updated_at = utcnow()
    others = [i for i in await _live_session_ids(db, person.id) if i != keep_session_id]
    if others:
        await db.execute(
            update(AuthSession).where(AuthSession.id.in_(others)).values(revoked_at=utcnow())
        )
    record(db, "owner.credentials_set" if new_email else "member.password_changed",
           Actor(person.role, person.id, person.display_name), subject=person)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise PeopleError(409, "email_taken", "That email already belongs to someone else.") from None
    live.close_sessions(others)
    return person


# --- ownership --------------------------------------------------------------


async def check_transfer(db: AsyncSession, actor: Actor, target_id: int) -> tuple[Person, Person]:
    """The current owner and the person who would become owner, or the reason it cannot happen.
    Both must be able to sign in with an email and password: the root passphrase is rotated
    during a transfer, so it is not a way back in for either of them."""
    if actor.role != "owner" or actor.person_id is None:
        raise PeopleError(403, "owner_only", "Only the owner can hand over the workspace.")
    owner = await get_active_person(db, actor.person_id)
    target = await get_person(db, target_id)
    if owner is None or owner.role != "owner":
        raise PeopleError(403, "owner_only", "Only the owner can hand over the workspace.")
    if target is None or target.status != "active":
        raise PeopleError(404, "person_not_found", "No such member.")
    if target.id == owner.id:
        raise PeopleError(409, "already_owner", "You are already the owner.")
    if not owner.email or not owner.password_hash:
        raise PeopleError(409, "owner_needs_password", "Set an email and password for yourself first, so you can sign in after the handover.")
    if not target.password_hash:
        raise PeopleError(409, "target_needs_password", "That person has no password yet.")
    return owner, target


async def transfer_ownership(db: AsyncSession, actor: Actor, target_id: int) -> Person:
    """Make `target_id` the owner and the previous owner an admin, in one transaction. The route
    rotates the root passphrase first, so a failure here leaves the owner in place and the old
    passphrase already dead rather than the reverse."""
    await begin_write(db)
    try:
        owner, target = await check_transfer(db, actor, target_id)
    except PeopleError:
        await db.rollback()
        raise
    now = utcnow()
    owner.role = "admin"
    owner.updated_at = now
    target.role = "owner"
    target.updated_at = now
    record(db, "ownership.transferred", actor, subject=target, detail=f"from {owner.display_name}")
    await db.commit()
    return target
