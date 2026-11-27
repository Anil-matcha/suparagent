"""Hosted workspace grants: a request is accepted only with a grant signed for this workspace, the person is
mirrored for attribution, rights follow the grant's role and permissions, and local people management is off."""

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from app.auth import grant
from app.core.config import settings
from app.db.models.people import Person
from tests.auth.test_auth_api import app, auth_settings, client, factory  # noqa: F401
from tests.auth.test_people import clean_people  # noqa: F401

WORKSPACE = "u_workspace_a"
KEY = Ed25519PrivateKey.generate()
OTHER_KEY = Ed25519PrivateKey.generate()


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def public(key):
    return b64(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def make(role="member", perms=None, sub="user-1", name="Ada", email="ada@acme.dev", aud=WORKSPACE, key=KEY, kid="k1",
         iat=None, ttl=30, head=None, drop=()):
    iat = int(time.time()) if iat is None else iat
    claims = {"iss": "dots-control", "aud": aud, "sub": sub, "role": role, "name": name, "email": email,
              "iat": iat, "exp": iat + ttl, "jti": "j"}
    if perms is not None:
        claims["perms"] = perms
    for field in drop:
        claims.pop(field)
    head_b64 = b64(json.dumps(head or {"v": 1, "kid": kid}, separators=(",", ":")).encode())
    body = b64(json.dumps(claims, separators=(",", ":")).encode())
    return f"{head_b64}.{body}.{b64(key.sign(f'{head_b64}.{body}'.encode()))}"


@pytest.fixture(autouse=True)
def grant_mode(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "HOSTED_WORKSPACE_ID", WORKSPACE)
    monkeypatch.setattr(settings, "HOSTED_GRANT_PUBLIC_KEYS", json.dumps({"k1": public(KEY)}))


def hdr(token):
    return {"X-Dots-Grant": token}


async def session(api, token):
    return await api.get("/api/v1/auth/session", headers=hdr(token))


async def test_a_valid_grant_signs_the_person_in_with_their_role(app, factory):  # noqa: F811
    async with client(app) as api:
        response = await session(api, make("admin"))
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["role"], body["display_name"], body["kind"]) == ("admin", "Ada", "grant")
        assert "admin" in body["scopes"]
    async with factory() as db:
        person = (await db.execute(select(Person).where(Person.external_id == "user-1"))).scalar_one()
        assert (person.email, person.role, person.status) == ("ada@acme.dev", "admin", "active")


async def test_no_grant_and_every_other_credential_are_refused(app, factory):  # noqa: F811
    async with client(app) as api:
        assert (await api.get("/api/v1/auth/session")).status_code == 401
        assert (await api.get("/api/v1/auth/session", headers={"Authorization": "Bearer x"})).status_code == 401
        assert (await api.get("/api/v1/auth/session", cookies={"od_session": "x"})).status_code == 401
        two = [(b"x-dots-grant", make().encode()), (b"x-dots-grant", make().encode())]
        assert (await api.get("/api/v1/auth/session", headers=two)).status_code == 401
        assert (await api.get("/api/v1/health")).status_code == 200  # public routes stay public


@pytest.mark.parametrize("token", [
    lambda: make(aud="u_workspace_b"),                       # another workspace
    lambda: make(key=OTHER_KEY),                             # signed by someone else
    lambda: make(kid="k2"),                                  # unknown key id
    lambda: make(iat=int(time.time()) - 500),                # expired
    lambda: make(iat=int(time.time()) + 500),                # not yet valid
    lambda: make(ttl=600),                                   # lifetime too long
    lambda: make(role="root"),                               # unknown role
    lambda: make(perms=["chat", "root"]),                    # unknown permission
    lambda: make(head={"v": 2, "kid": "k1"}),                # unknown version
    lambda: make(drop=("sub",)),                             # missing claim
    lambda: "not.a.grant",
    lambda: make()[:-4] + "AAAA",                            # tampered signature
])
async def test_bad_grants_are_refused(app, factory, token):  # noqa: F811
    async with client(app) as api:
        response = await session(api, token())
        assert response.status_code == 401
        assert response.json()["detail"]["error_code"] == "grant_invalid"
    async with factory() as db:
        assert (await db.execute(select(Person))).first() is None  # nothing was mirrored


async def test_a_viewer_can_read_but_not_write_and_a_member_without_chat_is_read_only(app, factory):  # noqa: F811
    async with client(app) as api:
        assert (await api.get("/api/v1/threads", headers=hdr(make("viewer")))).status_code == 200
        refused = await api.post("/api/v1/threads", headers=hdr(make("viewer")), json={})
        assert refused.status_code == 403
        refused = await api.post("/api/v1/threads", headers=hdr(make("member", perms=["files"])), json={})
        assert refused.status_code == 403


async def test_admin_scope_routes_need_an_admin_grant(app, factory):  # noqa: F811
    async with client(app) as api:
        assert (await api.get("/api/v1/people/audit", headers=hdr(make("member")))).status_code == 403
        assert (await api.get("/api/v1/people/audit", headers=hdr(make("admin")))).status_code == 200


async def test_role_and_permissions_follow_the_latest_grant(app, factory):  # noqa: F811
    async with client(app) as api:
        first = (await session(api, make("member", perms=["chat", "files"]))).json()
        assert first["permissions"] == ["chat", "files"]
        demoted = (await session(api, make("viewer"))).json()
        assert demoted["role"] == "viewer" and demoted["permissions"] == []
        renamed = (await session(api, make("viewer", name="Ada L", email="ada.l@acme.dev"))).json()
        assert renamed["display_name"] == "Ada L"
    async with factory() as db:
        rows = (await db.execute(select(Person))).scalars().all()
        assert len(rows) == 1 and rows[0].email == "ada.l@acme.dev"  # one person, refreshed, not duplicated


async def test_a_local_person_with_the_same_email_is_adopted(app, factory):  # noqa: F811
    async with factory() as db:
        db.add(Person(email="ada@acme.dev", display_name="Old", role="member", status="active"))
        await db.commit()
    async with client(app) as api:
        assert (await session(api, make("member"))).status_code == 200
    async with factory() as db:
        rows = (await db.execute(select(Person))).scalars().all()
        assert len(rows) == 1 and rows[0].external_id == "user-1"


async def test_only_one_owner_after_a_transfer(app, factory):  # noqa: F811
    async with client(app) as api:
        await session(api, make("owner", sub="a", email="a@acme.dev"))
        await session(api, make("owner", sub="b", email="b@acme.dev", name="Bo"))
    async with factory() as db:
        roles = {p.external_id: p.role for p in (await db.execute(select(Person))).scalars()}
        assert roles == {"a": "admin", "b": "owner"}


async def test_local_people_management_is_off_but_reading_the_roster_works(app, factory):  # noqa: F811
    token = make("owner")
    async with client(app) as api:
        assert (await api.get("/api/v1/people", headers=hdr(token))).status_code == 200
        for method, path, body in (
            ("POST", "/api/v1/people/invites", {"email": "x@acme.dev", "role": "member"}),
            ("DELETE", "/api/v1/people/1", None),
            ("PATCH", "/api/v1/people/1", {"role": "viewer"}),
            ("POST", "/api/v1/people/transfer", {"person_id": 1}),
        ):
            response = await api.request(method, path, headers=hdr(token), json=body)
            assert response.status_code == 409, (path, response.text)
        # Joining and member sign-in are public routes and are refused as well.
        assert (await api.post("/api/v1/auth/people/login", json={"email": "a@b.dev", "password": "x" * 12})).status_code == 409


async def test_without_the_settings_nothing_changes(app, factory, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "HOSTED_WORKSPACE_ID", "")
    async with client(app) as api:
        assert (await api.get("/api/v1/auth/session", headers=hdr(make()))).status_code == 401  # no cookie: ordinary rules


def test_enabled_needs_both_settings(monkeypatch):
    monkeypatch.setattr(settings, "HOSTED_GRANT_PUBLIC_KEYS", "")
    assert not grant.enabled()


def test_keys_may_be_written_as_pairs_for_an_env_file():
    other = Ed25519PrivateKey.generate()
    keys = grant.load_keys(f"k1={public(KEY)}, k2={public(other)}")
    assert set(keys) == {"k1", "k2"}
    claims = grant.verify(make(), keys, WORKSPACE)
    assert claims.sub == "user-1"
    with pytest.raises(grant.GrantError):
        grant.load_keys("not a key list")
    with pytest.raises(grant.GrantError):
        grant.load_keys("k1=@@@")
