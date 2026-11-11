"""Members of this workspace: roster, invitations, roles, removal, audit and joining.

The middleware has already checked Host, Origin, the session, its scope and (for a viewer)
that the request is a read. Everything here except the two public sign-in routes needs a
session; inviting and managing people needs admin. Not available on the desktop app, which
is one person on their own machine.
"""

import secrets
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import credentials, limiter, people, request_info, store
from app.auth.routes import (
    SessionInfo,
    _browser_session,
    _error,
    current_principal,
    require_admin,
    require_json,
    require_rate,
)
from app.auth.policy import Principal
from app.db.models.people import PeopleAudit, Person, PersonInvite
from app.db.session import get_db
from app.desktop import profile


def _not_on_desktop() -> None:
    if profile.active():
        raise _error(409, "people_unavailable", "The desktop app is for one person. Use a hosted or server install to share it.")


router = APIRouter(prefix="/people", tags=["People"], dependencies=[Depends(_not_on_desktop)])
join_router = APIRouter(prefix="/auth", tags=["Auth"], dependencies=[Depends(_not_on_desktop)])


def actor_of(principal: Principal) -> people.Actor:
    """Who is acting. A principal without a role that holds admin (a paired admin device, the
    local owner) has the owner's rights, as it already has over everything else."""
    if principal.role is not None:
        return people.Actor(principal.role, principal.person_id, principal.label or "")
    if principal.is_admin:
        return people.Actor("owner", None, principal.label or "Owner")
    return people.Actor("member", None, principal.label or "")


def _fail(exc: people.PeopleError) -> HTTPException:
    return _error(exc.status, exc.code, exc.message)


class PersonResponse(BaseModel):
    id: int
    display_name: str
    role: str
    # What they may do: chat, files, approve, rules, agents. Admins and the owner have all.
    permissions: list[str] = []
    # Only for admins: a member sees names and roles, not other people's addresses.
    email: str | None = None
    joined_at: datetime
    last_login_at: datetime | None = None
    is_you: bool = False


def _person(person: Person, principal: Principal) -> PersonResponse:
    return PersonResponse(
        id=person.id, display_name=person.display_name, role=person.role,
        permissions=list(people.person_permissions(person)),
        email=person.email if principal.is_admin else None,
        joined_at=person.created_at,
        last_login_at=person.last_login_at if principal.is_admin else None,
        is_you=principal.person_id == person.id,
    )


@router.get("", response_model=list[PersonResponse])
async def list_people(db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    await people.ensure_owner_person(db)
    return [_person(p, principal) for p in await people.list_people(db)]


class RenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=200)


@router.patch("/me", response_model=PersonResponse)
async def rename_me(
    body: RenameBody, db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)
):
    if principal.person_id is None:
        raise _error(409, "not_a_member", "Sign in as a member to set a name.")
    try:
        person = await people.rename_person(db, principal.person_id, body.display_name)
    except people.PeopleError as exc:
        raise _fail(exc) from exc
    return _person(person, principal)


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: str = Field(max_length=credentials.MAX_PASSPHRASE_LENGTH)
    # Everyone who has a password proves it. The owner of an install that predates people has
    # none yet: they confirm with the root passphrase and give the email they will sign in with.
    current_password: str | None = Field(default=None, max_length=credentials.MAX_PASSPHRASE_LENGTH)
    passphrase: str | None = Field(default=None, max_length=credentials.MAX_PASSPHRASE_LENGTH)
    email: str | None = Field(default=None, max_length=300)


@router.post("/me/password", status_code=204)
async def change_my_password(
    body: PasswordBody, request: Request, response: Response, db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(current_principal), source: str = Depends(require_rate),
):
    if principal.person_id is None:
        raise _error(409, "not_a_member", "Sign in as a member to set a password.")
    wait = limiter.login_lockout.retry_after(source)
    if wait:
        raise _error(429, "locked_out", f"Too many wrong attempts. Try again in {wait} seconds.",
                     {"Retry-After": str(wait)})
    root_verified = False
    if body.passphrase is not None:
        root_verified = await store.verify_owner(db, body.passphrase)
        if not root_verified:
            limiter.login_lockout.failure(source)
            raise _error(401, "wrong_passphrase", "That passphrase is not right.")
    try:
        await people.change_password(
            db, principal.person_id, new_password=body.new_password, current_password=body.current_password,
            email=body.email, root_verified=root_verified, keep_session_id=principal.session_id,
        )
    except people.PeopleError as exc:
        if exc.code == "wrong_password":
            limiter.login_lockout.failure(source)
        raise _fail(exc) from exc
    limiter.login_lockout.success(source)
    response.status_code = 204
    return response


class TransferBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: int
    passphrase: str = Field(max_length=credentials.MAX_PASSPHRASE_LENGTH)


@router.post("/transfer", response_model=PersonResponse)
async def transfer_ownership(
    body: TransferBody, response: Response, db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_admin), source: str = Depends(require_rate),
):
    """Hand the workspace to another member. The old owner becomes an admin and the root passphrase
    is replaced by one nobody knows (the server's operator can set a new one with the CLI), so the
    old owner's passphrase cannot sign in as the new owner."""
    actor = actor_of(principal)
    wait = limiter.login_lockout.retry_after(source)
    if wait:
        raise _error(429, "locked_out", f"Too many wrong attempts. Try again in {wait} seconds.",
                     {"Retry-After": str(wait)})
    try:
        await people.check_transfer(db, actor, body.person_id)
    except people.PeopleError as exc:
        raise _fail(exc) from exc
    if not await store.verify_owner(db, body.passphrase):
        limiter.login_lockout.failure(source)
        raise _error(401, "wrong_passphrase", "That passphrase is not right.")
    limiter.login_lockout.success(source)
    await store.set_passphrase(db, secrets.token_urlsafe(24), first_time_only=False)
    try:
        person = await people.transfer_ownership(db, actor, body.person_id)
    except people.PeopleError as exc:
        raise _fail(exc) from exc
    return _person(person, principal)


class InviteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=300)
    role: Literal["admin", "member", "viewer"] = "member"
    # For a member only; leave out for the usual set (chat, files, approve, agents).
    permissions: list[str] | None = Field(default=None, max_length=10)


class InviteResponse(BaseModel):
    id: int
    email: str
    role: str
    permissions: list[str] = []
    invited_by: str
    created_at: datetime
    expires_at: datetime
    # The link secret. Shown once, when the invitation is made; only its digest is kept.
    token: str | None = None


def _invite(invite: PersonInvite, token: str | None = None) -> InviteResponse:
    return InviteResponse(
        id=invite.id, email=invite.email, role=invite.role,
        permissions=list(people.effective_permissions(invite.role, invite.permissions)),
        invited_by=invite.invited_by_label,
        created_at=invite.created_at, expires_at=invite.expires_at, token=token,
    )


@router.post("/invites", response_model=InviteResponse, status_code=201)
async def create_invite(
    body: InviteBody, db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_admin), _: str = Depends(require_rate),
):
    try:
        token, invite = await people.create_invite(
            db, actor_of(principal), email=body.email, role=body.role, permissions=body.permissions)
    except people.PeopleError as exc:
        raise _fail(exc) from exc
    return _invite(invite, token)


@router.get("/invites", response_model=list[InviteResponse])
async def list_invites(db: AsyncSession = Depends(get_db), _: Principal = Depends(require_admin)):
    return [_invite(i) for i in await people.list_open_invites(db)]


@router.delete("/invites/{invite_id}", status_code=204)
async def revoke_invite(
    invite_id: int, response: Response, db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    try:
        revoked = await people.revoke_invite(db, actor_of(principal), invite_id)
    except people.PeopleError as exc:
        raise _fail(exc) from exc
    if not revoked:
        raise _error(404, "invite_not_found", "No such open invitation.")
    response.status_code = 204
    return response


class AuditResponse(BaseModel):
    id: int
    action: str
    actor: str
    subject: str
    detail: str
    at: datetime


@router.get("/audit", response_model=list[AuditResponse])
async def audit(limit: int = 100, db: AsyncSession = Depends(get_db), _: Principal = Depends(require_admin)):
    rows: list[PeopleAudit] = await people.list_audit(db, max(1, min(limit, 500)))
    return [
        AuditResponse(id=r.id, action=r.action, actor=r.actor_label, subject=r.subject_label,
                      detail=r.detail, at=r.created_at)
        for r in rows
    ]


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["admin", "member", "viewer"] | None = None
    permissions: list[str] | None = Field(default=None, max_length=10)


@router.patch("/{person_id}", response_model=PersonResponse)
async def update_member(
    person_id: int, body: UpdateBody, db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    if body.role is None and body.permissions is None:
        raise _error(422, "nothing_to_change", "Send a role, permissions, or both.")
    try:
        person = await people.update_member(
            db, actor_of(principal), person_id, role=body.role, permissions=body.permissions)
    except people.PeopleError as exc:
        raise _fail(exc) from exc
    return _person(person, principal)


@router.delete("/{person_id}", status_code=204)
async def remove_person(
    person_id: int, response: Response, db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    try:
        await people.remove_person(db, actor_of(principal), person_id)
    except people.PeopleError as exc:
        raise _fail(exc) from exc
    response.status_code = 204
    return response


# --- joining and signing in (public: no session yet) --------------------------


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(max_length=200)
    email: str = Field(max_length=300)
    display_name: str = Field(max_length=200)
    password: str = Field(max_length=credentials.MAX_PASSPHRASE_LENGTH)


class PersonLoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=300)
    password: str = Field(max_length=credentials.MAX_PASSPHRASE_LENGTH)


@join_router.post("/invites/accept", response_model=SessionInfo, dependencies=[Depends(require_json)])
async def accept_invite(
    body: AcceptBody, request: Request, response: Response,
    db: AsyncSession = Depends(get_db), source: str = Depends(require_rate),
):
    wait = limiter.pair_lockout.retry_after(source)
    if wait:
        raise _error(429, "locked_out", f"Too many wrong invitations. Try again in {wait} seconds.",
                     {"Retry-After": str(wait)})
    try:
        person = await people.accept_invite(
            db, token=body.token.strip(), email=body.email, display_name=body.display_name, password=body.password
        )
    except people.InviteInvalid as exc:
        limiter.pair_lockout.failure(source)
        raise _fail(exc) from exc
    except people.PeopleError as exc:
        raise _fail(exc) from exc
    limiter.pair_lockout.success(source)
    label = request_info.label_from_user_agent(request.headers.get("user-agent"))
    return await _browser_session(request, response, db, label=label, scopes="", person=person)


@join_router.post("/people/login", response_model=SessionInfo, dependencies=[Depends(require_json)])
async def person_login(
    body: PersonLoginBody, request: Request, response: Response,
    db: AsyncSession = Depends(get_db), source: str = Depends(require_rate),
):
    wait = limiter.login_lockout.retry_after(source)
    if wait:
        raise _error(429, "locked_out", f"Too many wrong passwords. Try again in {wait} seconds.",
                     {"Retry-After": str(wait)})
    person = await people.authenticate_person(db, body.email, body.password)
    if person is None:
        limiter.login_lockout.failure(source)
        raise _error(401, "wrong_credentials", "That email or password is not right.")
    limiter.login_lockout.success(source)
    label = request_info.label_from_user_agent(request.headers.get("user-agent"))
    return await _browser_session(request, response, db, label=label, scopes="", person=person)
