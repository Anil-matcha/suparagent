"""Hosted workspace grants: how a hosted control plane tells this server who is acting.

Off unless HOSTED_WORKSPACE_ID and HOSTED_GRANT_PUBLIC_KEYS are set, so self-hosted installs and the desktop
app never see it. When on, every request must carry `X-Dots-Grant`: a compact token signed (Ed25519, pinned) by
the control plane for exactly this workspace. The server holds only public keys, so it cannot mint grants. The
person in the grant is mirrored into the `person` table (by `external_id`) so attribution, rosters and history
work as they do for local people, and rights come from the grant's role and permissions on every request.

Token: base64url(header).base64url(claims).base64url(signature); header {"v":1,"kid":...}.
"""

import base64
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import people
from app.auth.policy import Principal
from app.core.config import settings
from app.db.models.people import PERMISSIONS, ROLES, Person
from app.db.transactions import begin_write

HEADER = b"x-dots-grant"
ISSUER = "dots-control"
VERSION = 1
MAX_TTL = 60
SKEW = 30
NAME_MAX = 80
_REQUIRED = {"iss": str, "aud": str, "sub": str, "role": str, "name": str, "email": str, "iat": int, "exp": int, "jti": str}


class GrantError(Exception):
    """Why a grant was refused. For logs; the caller only learns that it was refused."""


@dataclass(frozen=True)
class Claims:
    sub: str
    role: str
    name: str
    email: str
    perms: tuple[str, ...] | None  # None: the role's usual set


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def enabled() -> bool:
    return bool(settings.HOSTED_WORKSPACE_ID and settings.HOSTED_GRANT_PUBLIC_KEYS)


def load_keys(raw: str) -> dict[str, Ed25519PublicKey]:
    try:
        table = json.loads(raw)
        return {kid: Ed25519PublicKey.from_public_bytes(_unb64(key)) for kid, key in table.items()}
    except (ValueError, TypeError, AttributeError) as exc:
        raise GrantError("HOSTED_GRANT_PUBLIC_KEYS is not a JSON map of key id to base64url Ed25519 public key") from exc


_keys_cache: tuple[str, dict[str, Ed25519PublicKey]] | None = None


def _keys() -> dict[str, Ed25519PublicKey]:
    global _keys_cache
    raw = settings.HOSTED_GRANT_PUBLIC_KEYS
    if _keys_cache is None or _keys_cache[0] != raw:
        _keys_cache = (raw, load_keys(raw))
    return _keys_cache[1]


def verify(token: str, keys: Mapping[str, Ed25519PublicKey], workspace_id: str, now: int | None = None) -> Claims:
    """The claims of a valid grant for `workspace_id`; anything else raises GrantError."""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        head = json.loads(_unb64(head_b64))
        claims = json.loads(_unb64(body_b64))
        signature = _unb64(sig_b64)
    except (ValueError, TypeError) as exc:
        raise GrantError("malformed") from exc
    if not isinstance(head, dict) or head.get("v") != VERSION:
        raise GrantError("unknown version")
    kid = head.get("kid")
    key = keys.get(kid) if isinstance(kid, str) else None
    if key is None:
        raise GrantError("unknown key")
    try:
        key.verify(signature, f"{head_b64}.{body_b64}".encode("ascii"))
    except (InvalidSignature, UnicodeEncodeError) as exc:
        raise GrantError("bad signature") from exc
    if not isinstance(claims, dict):
        raise GrantError("malformed")
    for name, kind in _REQUIRED.items():
        value = claims.get(name)
        if not isinstance(value, kind) or isinstance(value, bool) or (kind is str and not value):
            raise GrantError(f"claim {name}")
    if claims["iss"] != ISSUER:
        raise GrantError("issuer")
    if claims["aud"] != workspace_id:
        raise GrantError("another workspace")
    if claims["role"] not in ROLES:
        raise GrantError("role")
    perms = claims.get("perms")
    if perms is not None and (not isinstance(perms, list) or any(p not in PERMISSIONS for p in perms)):
        raise GrantError("perms")
    if claims["exp"] <= claims["iat"] or claims["exp"] - claims["iat"] > MAX_TTL:
        raise GrantError("lifetime")
    t = int(time.time()) if now is None else now
    if t > claims["exp"] + SKEW or t < claims["iat"] - SKEW:
        raise GrantError("expired or not yet valid")
    return Claims(claims["sub"], claims["role"], claims["name"][:NAME_MAX], claims["email"].strip().lower(),
                  tuple(perms) if perms is not None else None)


def _stored(claims: Claims) -> str | None:
    """The `permissions` column value: only a member carries a list."""
    if claims.role != "member" or claims.perms is None:
        return None
    return people.encode_permissions(claims.perms)


async def mirror_person(db: AsyncSession, claims: Claims) -> Person:
    """The person row for this grant, created or refreshed. Writes only when something changed."""
    person = (await db.execute(select(Person).where(Person.external_id == claims.sub))).scalar_one_or_none()
    if person is None:
        # Adopt a local person with the same email rather than failing on the unique index.
        person = (await db.execute(select(Person).where(Person.email == claims.email))).scalar_one_or_none()
    stored = _stored(claims)
    if (person is not None and person.external_id == claims.sub and person.status == "active"
            and (person.display_name, person.email, person.role, person.permissions)
            == (claims.name, claims.email, claims.role, stored)):
        return person
    try:
        return await _write_mirror(db, claims, person, stored)
    except IntegrityError:  # a concurrent first request made the row: use theirs
        await db.rollback()
        return await mirror_person(db, claims)


async def _write_mirror(db: AsyncSession, claims: Claims, person: Person | None, stored: str | None) -> Person:
    await begin_write(db)
    if person is None:
        person = Person(external_id=claims.sub, display_name=claims.name, email=claims.email, role=claims.role,
                        permissions=stored, status="active")
        db.add(person)
    else:
        person.external_id, person.display_name, person.email = claims.sub, claims.name, claims.email
        person.role, person.permissions, person.status, person.removed_at = claims.role, stored, "active", None
        person.updated_at = people.utcnow()
    if claims.role == "owner":  # one owner: a previous owner's mirror becomes an admin until its next grant
        others = (await db.execute(select(Person).where(
            Person.role == "owner", Person.external_id.is_not(None), Person.external_id != claims.sub))).scalars()
        for other in others:
            other.role = "admin"
    await db.flush()
    await db.commit()
    return person


async def principal_for_grant(db: AsyncSession, token: str) -> Principal:
    claims = verify(token, _keys(), settings.HOSTED_WORKSPACE_ID)
    person = await mirror_person(db, claims)
    return people.principal_for(person, kind="grant", session_id=None)
