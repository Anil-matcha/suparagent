"""Browser sign-in (OAuth with PKCE) for owner-added MCP servers, against a fake authorization server.

No network: discovery, registration, token, refresh and revocation requests go to an in-memory
httpx MockTransport swapped in for the module's client factory."""
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app import community_mcp as mcp
from app import custom_mcp_oauth as oauth
from app.db.models import Agent, CustomMcpConnection, CustomMcpOAuthFlow, CustomMcpToolGrant, UserCredential
from tests.routers.test_custom_mcp_api import (  # noqa: F401
    PATH, URL, admin, agent_id, app, auth_settings, client, code_of, factory, login, make, owner,
    pairing_code, server, set_owner, tool,
)

AUTH = "https://auth.example.com"
CALLBACK = "/api/v1/integrations/custom-mcp/oauth/callback"


class FakeAuthServer:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.challenge: str | None = None
        self.redirect_uri: str | None = None
        self.code = "code-123"
        self.access, self.refresh, self.expires_in = "at-1", "rt-1", 3600
        self.rotations = 0
        self.metadata_overrides: dict = {}
        self.registration = True
        self.token_error: str | None = None
        self.refresh_error: str | None = None

    def metadata(self):
        data = {
            "issuer": AUTH, "authorization_endpoint": f"{AUTH}/authorize", "token_endpoint": f"{AUTH}/token",
            "revocation_endpoint": f"{AUTH}/revoke", "code_challenge_methods_supported": ["S256"],
            "scopes_supported": ["read"],
        }
        if self.registration:
            data["registration_endpoint"] = f"{AUTH}/register"
        return {**data, **self.metadata_overrides}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = {}
        if request.method == "POST":
            body = json.loads(request.content) if request.headers.get("content-type", "").startswith("application/json") \
                else {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((request.method, url, body))
        if url.startswith("https://tools.example.com/.well-known/oauth-protected-resource"):
            if url.endswith("/mcp"):
                return httpx.Response(404)
            return httpx.Response(200, json={"authorization_servers": [AUTH], "scopes_supported": ["read", "write"]})
        if url == f"{AUTH}/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=self.metadata())
        if url == f"{AUTH}/register":
            return httpx.Response(201, json={"client_id": "dyn-client"})
        if url == f"{AUTH}/token":
            return self.token(body)
        if url == f"{AUTH}/revoke":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    def token(self, body):
        if body.get("grant_type") == "authorization_code":
            verifier = body.get("code_verifier", "")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if (self.token_error or body.get("code") != self.code or challenge != self.challenge
                    or body.get("redirect_uri") != self.redirect_uri):
                return httpx.Response(400, json={"error": self.token_error or "invalid_grant", "error_description": "SECRET-DETAIL"})
            return httpx.Response(200, json={"access_token": self.access, "token_type": "Bearer",
                                             "expires_in": self.expires_in, "refresh_token": self.refresh})
        if body.get("grant_type") == "refresh_token":
            if self.refresh_error or body.get("refresh_token") != self.refresh:
                return httpx.Response(400, json={"error": self.refresh_error or "invalid_grant"})
            self.rotations += 1
            self.access, self.refresh = f"at-{self.rotations + 1}", f"rt-{self.rotations + 1}"
            return httpx.Response(200, json={"access_token": self.access, "token_type": "bearer",
                                             "expires_in": 3600, "refresh_token": self.refresh})
        return httpx.Response(400, json={"error": "unsupported_grant_type"})


@pytest.fixture
def idp(monkeypatch):
    fake = FakeAuthServer()
    monkeypatch.setattr(oauth, "_client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


async def oauth_connection(admin, **extra):
    return await make(admin, auth_mode="oauth", **extra)


async def start(admin, conn, idp):
    response = await admin.post(f"{PATH}/connections/{conn['id']}/oauth/start", json={"revision": conn["revision"]})
    assert response.status_code == 200, response.text
    url = response.json()["authorization_url"]
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    idp.challenge, idp.redirect_uri = query["code_challenge"], query["redirect_uri"]
    return url, query


async def sign_in(admin, idp, **extra):
    conn = await oauth_connection(admin, **extra)
    url, query = await start(admin, conn, idp)
    done = await admin.get(f"{CALLBACK}?state={query['state']}&code={idp.code}")
    assert done.status_code == 200, done.text
    latest = [c for c in (await admin.get(f"{PATH}/connections")).json() if c["id"] == conn["id"]][0]
    return latest, query


async def stored_secrets(factory, owner):
    async with factory() as db:
        return (await db.execute(select(UserCredential).where(UserCredential.user_id == owner))).scalars().all()


async def test_a_new_oauth_connection_needs_sign_in_and_cannot_be_tested(admin, idp):
    conn = await oauth_connection(admin)
    assert conn["auth_mode"] == "oauth" and conn["state"] == "needs_sign_in" and conn["oauth_signed_in"] is False
    refused = await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": conn["revision"]})
    assert refused.status_code == 409 and code_of(refused) == "needs_sign_in"


async def test_start_discovers_registers_and_builds_a_pkce_s256_url(admin, idp, factory):
    conn = await oauth_connection(admin, oauth_scopes="read  write")
    url, query = await start(admin, conn, idp)
    assert url.startswith(f"{AUTH}/authorize?")
    assert query["response_type"] == "code" and query["code_challenge_method"] == "S256"
    assert query["client_id"] == "dyn-client" and query["scope"] == "read write" and query["resource"] == URL
    assert query["redirect_uri"].endswith(CALLBACK) and len(query["state"]) >= 40
    registration = next(body for method, url_, body in idp.calls if url_ == f"{AUTH}/register")
    assert registration["token_endpoint_auth_method"] == "none" and registration["redirect_uris"] == [query["redirect_uri"]]
    async with factory() as db:
        [flow] = (await db.execute(select(CustomMcpOAuthFlow))).scalars().all()
        assert flow.state_digest == hashlib.sha256(query["state"].encode()).hexdigest() and query["state"] not in flow.state_digest
        assert flow.verifier_cipher and "verifier" not in flow.verifier_cipher


async def test_a_second_start_reuses_the_registered_client(admin, idp):
    conn = await oauth_connection(admin)
    await start(admin, conn, idp)
    await start(admin, conn, idp)
    assert sum(1 for _, url, _ in idp.calls if url == f"{AUTH}/register") == 1


async def test_the_callback_exchanges_the_code_and_stores_encrypted_tokens(admin, idp, factory, owner, server):
    latest, _ = await sign_in(admin, idp)
    assert latest["oauth_signed_in"] is True and latest["state"] == "not_tested" and latest["enabled"] is False
    text = json.dumps(latest)
    assert "at-1" not in text and "rt-1" not in text
    for row in await stored_secrets(factory, owner):
        assert "at-1" not in row.ciphertext and "rt-1" not in row.ciphertext
    tested = await admin.post(f"{PATH}/connections/{latest['id']}/test", json={"revision": latest["revision"]})
    assert tested.status_code == 200 and tested.json()["state"] == "disabled"
    assert server.seen[-1] == (URL, "at-1", None)


async def test_a_state_works_once_and_a_wrong_or_malformed_one_does_nothing(admin, idp, factory):
    conn = await oauth_connection(admin)
    _, query = await start(admin, conn, idp)
    for bad in ("", "short", "x" * 43, "é" * 43):
        refused = await admin.get(f"{CALLBACK}", params={"state": bad, "code": idp.code})
        assert refused.status_code == 400
    ok = await admin.get(f"{CALLBACK}?state={query['state']}&code={idp.code}")
    assert ok.status_code == 200
    replay = await admin.get(f"{CALLBACK}?state={query['state']}&code={idp.code}")
    assert replay.status_code == 400
    assert sum(1 for _, url, _ in idp.calls if url == f"{AUTH}/token") == 1


async def test_an_expired_cancelled_or_changed_sign_in_stores_nothing(admin, idp, factory, owner):
    conn = await oauth_connection(admin)
    _, query = await start(admin, conn, idp)
    async with factory() as db:
        flow = (await db.execute(select(CustomMcpOAuthFlow))).scalar_one()
        flow.expires_at = flow.expires_at.replace(year=2000)
        await db.commit()
    assert (await admin.get(f"{CALLBACK}?state={query['state']}&code={idp.code}")).status_code == 400
    _, query = await start(admin, conn, idp)
    assert (await admin.get(f"{CALLBACK}?state={query['state']}&error=access_denied")).status_code == 400
    _, query = await start(admin, conn, idp)
    renamed = await admin.patch(f"{PATH}/connections/{conn['id']}", json={"revision": conn["revision"], "name": "Other"})
    assert renamed.status_code == 200
    changed = await admin.get(f"{CALLBACK}?state={query['state']}&code={idp.code}")
    assert changed.status_code == 409
    assert not [row for row in await stored_secrets(factory, owner) if row.provider.startswith(oauth.CREDENTIAL_PREFIX)]
    assert sum(1 for _, url, _ in idp.calls if url == f"{AUTH}/token") == 0


async def test_a_wrong_code_or_pkce_verifier_is_refused_without_echoing_the_server(admin, idp, factory, owner):
    conn = await oauth_connection(admin)
    _, query = await start(admin, conn, idp)
    idp.code = "different"
    refused = await admin.get(f"{CALLBACK}?state={query['state']}&code=wrong-code")
    assert refused.status_code in (400, 502) and "SECRET-DETAIL" not in refused.text
    assert not [row for row in await stored_secrets(factory, owner) if row.provider.startswith(oauth.CREDENTIAL_PREFIX)]


@pytest.mark.parametrize("override, code", [
    ({"code_challenge_methods_supported": ["plain"]}, "oauth_not_supported"),
    ({"code_challenge_methods_supported": None}, "oauth_not_supported"),
    ({"issuer": "https://evil.example.com"}, "oauth_discovery_failed"),
    ({"authorization_endpoint": "http://auth.example.com/authorize"}, "unsafe_endpoint"),
    ({"token_endpoint": "https://10.0.0.5/token"}, "unsafe_endpoint"),
    ({"authorization_endpoint": "https://localhost/authorize"}, "unsafe_endpoint"),
])
async def test_unsafe_or_unsupported_authorization_servers_are_refused(admin, idp, override, code):
    idp.metadata_overrides = override
    conn = await oauth_connection(admin)
    refused = await admin.post(f"{PATH}/connections/{conn['id']}/oauth/start", json={"revision": conn["revision"]})
    assert refused.status_code in (400, 422, 502) and code_of(refused) == code


async def test_a_server_without_registration_needs_a_client_id_and_a_manual_one_is_used(admin, idp):
    idp.registration = False
    conn = await oauth_connection(admin)
    refused = await admin.post(f"{PATH}/connections/{conn['id']}/oauth/start", json={"revision": conn["revision"]})
    assert code_of(refused) == "oauth_client_required"
    manual = await oauth_connection(admin, oauth_client_id="my-client", credential="client-secret-value")
    assert "client-secret-value" not in json.dumps(manual) and manual["oauth_client_id"] == "my-client"
    _, query = await start(admin, manual, idp)
    assert query["client_id"] == "my-client"
    done = await admin.get(f"{CALLBACK}?state={query['state']}&code={idp.code}")
    assert done.status_code == 200
    token_call = next(body for _, url, body in idp.calls if url == f"{AUTH}/token")
    assert token_call["client_id"] == "my-client" and token_call["client_secret"] == "client-secret-value"
    assert not any(url == f"{AUTH}/register" for _, url, _ in idp.calls[2:])


async def test_an_expiring_token_is_refreshed_and_the_rotated_refresh_token_is_kept(admin, idp, factory, owner, server):
    latest, _ = await sign_in(admin, idp)
    async with factory() as db:
        row = (await db.execute(select(UserCredential).where(UserCredential.provider == oauth.provider(latest["id"])))).scalar_one()
        record, secret = oauth.unpack(row.ciphertext)
        record["expires_at"] = int(time.time()) + 5  # inside the refresh margin
        row.ciphertext = oauth.crypto.encrypt(oauth.pack(record, secret))
        await db.commit()
    tested = await admin.post(f"{PATH}/connections/{latest['id']}/test", json={"revision": latest["revision"]})
    assert tested.status_code == 200 and server.seen[-1][1] == "at-2" and idp.rotations == 1
    again = await admin.post(f"{PATH}/connections/{latest['id']}/test", json={"revision": tested.json()["revision"]})
    assert again.status_code == 200 and idp.rotations == 1, "a fresh token must not be refreshed again"
    async with factory() as db:
        row = (await db.execute(select(UserCredential).where(UserCredential.provider == oauth.provider(latest["id"])))).scalar_one()
        assert oauth.unpack(row.ciphertext)[0]["refresh_token"] == "rt-2"


async def test_a_dead_refresh_token_signs_the_connection_out_and_disables_it(admin, idp, factory, owner, server):
    latest, _ = await sign_in(admin, idp)
    ready = await admin.post(f"{PATH}/connections/{latest['id']}/test", json={"revision": latest["revision"]})
    enabled = await admin.post(f"{PATH}/connections/{latest['id']}/enable", json={"revision": ready.json()["revision"]})
    assert enabled.json()["enabled"] is True
    async with factory() as db:
        row = (await db.execute(select(UserCredential).where(UserCredential.provider == oauth.provider(latest["id"])))).scalar_one()
        record, secret = oauth.unpack(row.ciphertext)
        record["expires_at"] = int(time.time()) - 10
        row.ciphertext = oauth.crypto.encrypt(oauth.pack(record, secret))
        await db.commit()
    idp.refresh_error = "invalid_grant"
    failed = await admin.post(f"{PATH}/connections/{latest['id']}/test", json={"revision": enabled.json()["revision"]})
    assert failed.status_code == 409 and code_of(failed) == "needs_sign_in"
    after = [c for c in (await admin.get(f"{PATH}/connections")).json() if c["id"] == latest["id"]][0]
    assert after["enabled"] is False and after["state"] == "needs_sign_in" and after["oauth_signed_in"] is False


async def test_sign_out_revokes_both_tokens_and_clears_grants(admin, idp, factory, owner, server):
    latest, _ = await sign_in(admin, idp)
    tested = await admin.post(f"{PATH}/connections/{latest['id']}/test", json={"revision": latest["revision"]})
    enabled = await admin.post(f"{PATH}/connections/{latest['id']}/enable", json={"revision": tested.json()["revision"]})
    agent = await agent_id(factory, owner)
    revision = enabled.json()["revision"]
    granted = await admin.put(f"{PATH}/connections/{latest['id']}/agents/{agent}/grants", json={
        "revision": revision, "tools": [{"name": "search", "schema_digest": tool("search")["schema_digest"]}]})
    assert granted.status_code == 200
    out = await admin.post(f"{PATH}/connections/{latest['id']}/oauth/sign-out", json={"revision": revision})
    body = out.json()
    assert body["oauth_signed_in"] is False and body["enabled"] is False and body["state"] == "needs_sign_in"
    revoked = [data["token"] for _, url, data in idp.calls if url == f"{AUTH}/revoke"]
    assert sorted(revoked) == ["at-1", "rt-1"]
    assert (await admin.get(f"{PATH}/connections/{latest['id']}/agents/{agent}/grants")).json() == {"tool_names": []}
    assert not [r for r in await stored_secrets(factory, owner) if r.provider.startswith(oauth.CREDENTIAL_PREFIX)]


async def test_sign_out_still_completes_when_the_provider_cannot_revoke(admin, idp, factory, owner):
    idp.metadata_overrides = {"revocation_endpoint": None}
    latest, _ = await sign_in(admin, idp)
    out = await admin.post(f"{PATH}/connections/{latest['id']}/oauth/sign-out", json={"revision": latest["revision"]})
    assert out.status_code == 200 and out.json()["oauth_signed_in"] is False


async def test_changing_the_address_or_client_drops_the_sign_in(admin, idp, factory, owner):
    latest, _ = await sign_in(admin, idp)
    moved = await admin.patch(f"{PATH}/connections/{latest['id']}", json={
        "revision": latest["revision"], "endpoint": "https://other.example.com/mcp"})
    assert moved.json()["oauth_signed_in"] is False
    assert not [r for r in await stored_secrets(factory, owner) if r.provider.startswith(oauth.CREDENTIAL_PREFIX)]
    again, _ = await sign_in(admin, idp)
    client_change = await admin.patch(f"{PATH}/connections/{again['id']}", json={
        "revision": again["revision"], "oauth_client_id": "another-client"})
    assert client_change.json()["oauth_signed_in"] is False and client_change.json()["oauth_client_id"] == "another-client"


async def test_deleting_the_connection_removes_tokens_and_pending_sign_ins(admin, idp, factory, owner):
    latest, _ = await sign_in(admin, idp)
    await start(admin, latest, idp)
    assert (await admin.delete(f"{PATH}/connections/{latest['id']}")).status_code == 204
    assert not [r for r in await stored_secrets(factory, owner) if r.provider.startswith(oauth.CREDENTIAL_PREFIX)]
    async with factory() as db:
        assert (await db.execute(select(CustomMcpOAuthFlow))).first() is None


async def test_oauth_settings_are_validated(admin):
    for body, code in (
        ({"oauth_client_id": "has space"}, "invalid_oauth_client"),
        ({"oauth_scopes": "é"}, "invalid_oauth_scopes"),
    ):
        refused = await admin.post(f"{PATH}/connections", json={"name": "X", "endpoint": URL, "auth_mode": "oauth", **body})
        assert refused.status_code == 422 and code_of(refused) == code
    plain = await admin.post(f"{PATH}/connections", json={"name": "X", "endpoint": URL, "oauth_client_id": "ignored"})
    assert plain.status_code == 201 and plain.json()["oauth_client_id"] is None


async def test_the_callback_needs_a_signed_in_owner(app, factory, idp, owner, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    await set_owner(factory)
    async with client(app) as api:
        assert (await api.get(f"{CALLBACK}?state={'a' * 43}&code=x")).status_code in (401, 403)


async def raw_get(port: int, target: str):
    import asyncio
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split()[1]), body.decode()


@pytest.fixture
async def desktop(monkeypatch):
    from types import SimpleNamespace
    from app import custom_mcp_oauth_loopback as loopback
    from app.routers import custom_mcp
    monkeypatch.setattr(custom_mcp, "profile", SimpleNamespace(active=lambda: True))
    yield
    for receiver in list(loopback._open.values()):
        await receiver.close()


async def test_desktop_sign_in_returns_to_a_loopback_listener_and_finishes_there(admin, idp, desktop, factory, owner, server):
    conn = await oauth_connection(admin)
    url, query = await start(admin, conn, idp)
    redirect = urlsplit(query["redirect_uri"])
    assert redirect.scheme == "http" and redirect.hostname == "127.0.0.1" and redirect.path == "/callback" and redirect.port
    registration = next(body for _, u, body in idp.calls if u == f"{AUTH}/register")
    assert registration["redirect_uris"] == [query["redirect_uri"]]
    status, page = await raw_get(redirect.port, f"/callback?state=wrong-state-{'x' * 30}&code={idp.code}")
    assert status == 400
    status, page = await raw_get(redirect.port, f"/callback?state={query['state']}&code={idp.code}")
    assert status == 200 and "Signed in" in page
    latest = [c for c in (await admin.get(f"{PATH}/connections")).json() if c["id"] == conn["id"]][0]
    assert latest["oauth_signed_in"] is True and "at-1" not in json.dumps(latest)
    tested = await admin.post(f"{PATH}/connections/{conn['id']}/test", json={"revision": latest["revision"]})
    assert tested.status_code == 200 and server.seen[-1][1] == "at-1"
    try:
        await raw_get(redirect.port, f"/callback?state={query['state']}&code={idp.code}")
        closed = False
    except OSError:
        closed = True
    assert closed, "the listener must close after a successful sign-in"


async def test_a_failed_discovery_leaves_no_listener_behind(admin, idp, desktop):
    from app import custom_mcp_oauth_loopback as loopback
    idp.metadata_overrides = {"code_challenge_methods_supported": ["plain"]}
    conn = await oauth_connection(admin)
    refused = await admin.post(f"{PATH}/connections/{conn['id']}/oauth/start", json={"revision": conn["revision"]})
    assert refused.status_code == 422 and not loopback._open
