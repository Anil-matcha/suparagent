"""Browser OAuth (authorization code + PKCE S256) for owner-added MCP servers.

Every request here goes to a destination that passed the same public-HTTPS checks as the MCP
endpoint itself, through the same pinned transport (no redirects, no private addresses). Tokens
and client secrets live only in the encrypted credential store; the provider CLI never sees them.
Error text is fixed wording: nothing a remote server says is echoed back.
"""

import asyncio
import base64
import hashlib
import json
import secrets
import time
import weakref
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import delete, select

from app import community_mcp as mcp
from app.core import crypto
from app.db.models import CustomMcpConnection, UserCredential
from app.db.transactions import begin_write, release_snapshot

CREDENTIAL_PREFIX = "integration:custom-mcp-oauth:"
MAX_DOC_BYTES = 64_000
FLOW_TTL_SECONDS = 600
REFRESH_MARGIN_SECONDS = 60
MAX_SCOPE_LENGTH = 500
_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


class OAuthError(mcp.McpError):
    pass


def provider(connection_id: str) -> str:
    return CREDENTIAL_PREFIX + connection_id


def _lock(connection_id: str) -> asyncio.Lock:
    lock = _locks.get(connection_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[connection_id] = lock
    return lock


def new_client() -> httpx.AsyncClient:
    """The only way this module reaches the network. Tests replace it."""
    return httpx.AsyncClient(
        transport=mcp.PinnedHTTPSTransport(), timeout=httpx.Timeout(15, connect=8),
        follow_redirects=False, trust_env=False,
    )


_client_factory = new_client


# ------------------------------------------------------------------ small helpers

def _public(url: object, label: str) -> str:
    mcp._validate_public_url(url, label, max_length=500)
    return str(url)


def _same_origin_issuer(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


async def _json_response(response: httpx.Response, what: str) -> dict:
    if 300 <= response.status_code < 400:
        raise OAuthError("redirect_denied", f"The {what} redirected; redirects are blocked.", 502)
    pieces, size = [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_DOC_BYTES:
            raise OAuthError("oauth_response_too_large", f"The {what} response is too large.", 502)
        pieces.append(chunk)
    return mcp._json_bytes(b"".join(pieces), limit=MAX_DOC_BYTES, source=what)


async def _get_json(client: httpx.AsyncClient, url: str, what: str) -> dict | None:
    try:
        async with asyncio.timeout(20):
            async with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise OAuthError("oauth_discovery_failed", f"The {what} could not be read.", 502)
                value = await _json_response(response, what)
    except mcp.McpError:
        raise
    except Exception:
        raise OAuthError("oauth_discovery_failed", f"The {what} could not be reached.", 502) from None
    if not isinstance(value, dict):
        raise OAuthError("oauth_discovery_failed", f"The {what} is not valid.", 502)
    return value


async def _post_form(client: httpx.AsyncClient, url: str, data: dict, what: str, *, as_json: bool = False) -> tuple[int, dict]:
    try:
        async with asyncio.timeout(20):
            kwargs = {"json": data} if as_json else {"data": data}
            async with client.stream("POST", url, headers={"Accept": "application/json"}, **kwargs) as response:
                status = response.status_code
                try:
                    body = await _json_response(response, what)
                except mcp.McpError:
                    if status >= 400:
                        return status, {}
                    raise
    except mcp.McpError:
        raise
    except Exception:
        raise OAuthError("oauth_unreachable", f"The {what} could not be reached.", 502) from None
    return status, body if isinstance(body, dict) else {}


# ------------------------------------------------------------------ discovery

async def discover(endpoint: str) -> dict:
    """Find the authorization server for an MCP endpoint (RFC 9728, then RFC 8414 / OIDC)."""
    parts = urlsplit(endpoint)
    origin = f"https://{parts.netloc}"
    path = parts.path.rstrip("/")
    client = _client_factory()
    try:
        resource = None
        for url in (f"{origin}/.well-known/oauth-protected-resource{path}", f"{origin}/.well-known/oauth-protected-resource"):
            resource = await _get_json(client, url, "protected-resource document")
            if resource is not None:
                break
        issuer = origin
        resource_scopes = None
        if resource is not None:
            servers = resource.get("authorization_servers")
            if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
                raise OAuthError("oauth_discovery_failed", "The server does not name an authorization server.", 502)
            issuer = _public(servers[0], "authorization server")
            scopes = resource.get("scopes_supported")
            if isinstance(scopes, list) and all(isinstance(item, str) for item in scopes):
                resource_scopes = scopes[:50]
        issuer_parts = urlsplit(issuer)
        issuer_origin = f"{issuer_parts.scheme}://{issuer_parts.netloc}"
        issuer_path = issuer_parts.path.rstrip("/")
        metadata = None
        for url in (
            f"{issuer_origin}/.well-known/oauth-authorization-server{issuer_path}",
            f"{issuer_origin}/.well-known/openid-configuration{issuer_path}",
        ):
            metadata = await _get_json(client, url, "authorization server document")
            if metadata is not None:
                break
        if metadata is None:
            raise OAuthError("oauth_not_supported", "This server does not offer browser sign-in.", 422)
    finally:
        await client.aclose()
    if not isinstance(metadata.get("issuer"), str) or not _same_origin_issuer(metadata["issuer"], issuer):
        raise OAuthError("oauth_discovery_failed", "The authorization server does not match its address.", 502)
    methods = metadata.get("code_challenge_methods_supported")
    if not isinstance(methods, list) or "S256" not in methods:
        raise OAuthError("oauth_not_supported", "The authorization server does not support PKCE S256.", 422)
    config = {
        "issuer": issuer,
        "authorization_endpoint": _public(metadata.get("authorization_endpoint"), "authorization endpoint"),
        "token_endpoint": _public(metadata.get("token_endpoint"), "token endpoint"),
        "resource": endpoint,
    }
    for key, label in (("registration_endpoint", "registration endpoint"), ("revocation_endpoint", "revocation endpoint")):
        if metadata.get(key) is not None:
            config[key] = _public(metadata[key], label)
    supported = resource_scopes or metadata.get("scopes_supported")
    if isinstance(supported, list) and all(isinstance(item, str) for item in supported):
        config["scopes_supported"] = supported[:50]
    return config


async def register_client(config: dict, redirect_uri: str) -> dict:
    """Dynamic client registration (RFC 7591) for a public PKCE client."""
    if "registration_endpoint" not in config:
        raise OAuthError("oauth_client_required", "This server needs a client ID. Enter one and try again.", 422)
    client = _client_factory()
    try:
        status, body = await _post_form(client, config["registration_endpoint"], {
            "client_name": "Suparagent", "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none",
        }, "registration endpoint", as_json=True)
    finally:
        await client.aclose()
    client_id = body.get("client_id")
    if status not in (200, 201) or not isinstance(client_id, str) or not 1 <= len(client_id) <= 500:
        raise OAuthError("oauth_registration_failed", "The server refused to register this app.", 502)
    secret = body.get("client_secret")
    return {"client_id": client_id, "client_secret": secret if isinstance(secret, str) and secret else None}


# ------------------------------------------------------------------ PKCE flow

def new_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def new_state() -> tuple[str, str]:
    """(value sent to the provider, digest we store)."""
    value = secrets.token_urlsafe(32)
    return value, hashlib.sha256(value.encode("ascii")).hexdigest()


def state_digest(value: str) -> str | None:
    if not 20 <= len(value) <= 100 or not value.isascii():
        return None
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def authorization_url(config: dict, *, client_id: str, redirect_uri: str, state: str, challenge: str, scope: str | None) -> str:
    query = {
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": config["resource"],
    }
    if scope:
        query["scope"] = scope
    return config["authorization_endpoint"] + ("&" if urlsplit(config["authorization_endpoint"]).query else "?") + urlencode(query)


def _token_record(body: dict, previous: dict | None = None) -> dict:
    access = body.get("access_token")
    token_type = body.get("token_type")
    if not isinstance(access, str) or not access or len(access) > 8192 or not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise OAuthError("oauth_token_invalid", "The authorization server returned an unusable token.", 502)
    if any(not 32 <= ord(char) < 127 for char in access):
        raise OAuthError("oauth_token_invalid", "The authorization server returned an unusable token.", 502)
    expires = body.get("expires_in")
    expires_at = None
    if isinstance(expires, (int, float)) and not isinstance(expires, bool) and 0 < expires <= 31_536_000:
        expires_at = int(time.time() + expires)
    refresh = body.get("refresh_token")
    if refresh is not None and (not isinstance(refresh, str) or not refresh or len(refresh) > 8192):
        raise OAuthError("oauth_token_invalid", "The authorization server returned an unusable token.", 502)
    return {
        "access_token": access, "expires_at": expires_at,
        "refresh_token": refresh or (previous or {}).get("refresh_token"),
    }


async def _token_request(config: dict, client_id: str, client_secret: str | None, fields: dict) -> dict:
    data = {**fields, "client_id": client_id, "resource": config["resource"]}
    if client_secret:
        data["client_secret"] = client_secret
    client = _client_factory()
    try:
        status, body = await _post_form(client, config["token_endpoint"], data, "token endpoint")
    finally:
        await client.aclose()
    if status >= 400:
        if body.get("error") == "invalid_grant":
            raise OAuthError("invalid_grant", "The sign-in was rejected or has expired. Sign in again.", 400)
        raise OAuthError("oauth_token_failed", "The authorization server refused the request.", 502)
    return body


async def exchange_code(config: dict, *, client_id: str, client_secret: str | None, code: str, verifier: str, redirect_uri: str) -> dict:
    body = await _token_request(config, client_id, client_secret, {
        "grant_type": "authorization_code", "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri,
    })
    return _token_record(body)


async def refresh_tokens(config: dict, *, client_id: str, client_secret: str | None, record: dict) -> dict:
    body = await _token_request(config, client_id, client_secret, {
        "grant_type": "refresh_token", "refresh_token": record["refresh_token"],
    })
    return _token_record(body, record)


async def revoke(config: dict, *, client_id: str, client_secret: str | None, record: dict) -> None:
    """Best effort: a provider without revocation, or one that fails, never blocks sign-out."""
    endpoint = config.get("revocation_endpoint")
    if not endpoint:
        return
    client = _client_factory()
    try:
        for token, hint in ((record.get("refresh_token"), "refresh_token"), (record.get("access_token"), "access_token")):
            if token:
                data = {"token": token, "token_type_hint": hint, "client_id": client_id}
                if client_secret:
                    data["client_secret"] = client_secret
                try:
                    await _post_form(client, endpoint, data, "revocation endpoint")
                except Exception:  # noqa: BLE001
                    pass
    finally:
        await client.aclose()


# ------------------------------------------------------------------ stored tokens

def pack(record: dict, client_secret: str | None) -> str:
    return json.dumps({**record, "client_secret": client_secret})


def unpack(ciphertext: str) -> tuple[dict, str | None] | None:
    try:
        value = json.loads(crypto.decrypt(ciphertext))
    except Exception:  # noqa: BLE001 - an unreadable secret counts as absent
        return None
    if not isinstance(value, dict) or not isinstance(value.get("access_token"), str):
        return None
    return value, value.get("client_secret") if isinstance(value.get("client_secret"), str) else None


async def save_record(db, owner_id: str, connection_id: str, record: dict, client_secret: str | None) -> None:
    row = await db.scalar(select(UserCredential).where(
        UserCredential.user_id == owner_id, UserCredential.provider == provider(connection_id)).with_for_update())
    ciphertext = crypto.encrypt(pack(record, client_secret))
    if row is None:
        db.add(UserCredential(user_id=owner_id, provider=provider(connection_id), file_path="-", ciphertext=ciphertext))
    else:
        row.file_path, row.ciphertext = "-", ciphertext


async def delete_record(db, owner_id: str, connection_id: str) -> None:
    await db.execute(delete(UserCredential).where(
        UserCredential.user_id == owner_id, UserCredential.provider == provider(connection_id)))


async def is_signed_in(db, owner_id: str, connection_id: str) -> bool:
    return await db.scalar(select(UserCredential.provider).where(
        UserCredential.user_id == owner_id, UserCredential.provider == provider(connection_id))) is not None


async def access_token(owner_id: str, connection_id: str, session_factory=None) -> str:
    """A usable access token, refreshed first when it is about to expire.

    Raises OAuthError("needs_sign_in") when there is no usable sign-in; after an `invalid_grant`
    the stored tokens are deleted so nothing keeps trying a dead refresh token."""
    if session_factory is None:
        from app.db.session import AsyncSessionLocal as session_factory
    async with _lock(connection_id):
        async with session_factory() as db:
            row = await db.scalar(select(UserCredential).where(
                UserCredential.user_id == owner_id, UserCredential.provider == provider(connection_id)))
            connection = await db.get(CustomMcpConnection, connection_id)
            unpacked = unpack(row.ciphertext) if row is not None else None
            config = connection.oauth_config if connection is not None and connection.owner_id == owner_id else None
            await release_snapshot(db)
        if unpacked is None or not config or "server" not in config:
            raise OAuthError("needs_sign_in", "Sign in to this server again.", 409)
        record, client_secret = unpacked
        expires_at = record.get("expires_at")
        if expires_at is None or expires_at - REFRESH_MARGIN_SECONDS > time.time():
            return record["access_token"]
        if not record.get("refresh_token"):
            raise OAuthError("needs_sign_in", "Sign in to this server again.", 409)
        try:
            fresh = await refresh_tokens(
                config["server"], client_id=config["effective_client_id"], client_secret=client_secret, record=record)
        except OAuthError as exc:
            if exc.code == "invalid_grant":
                async with session_factory() as db:
                    await begin_write(db)
                    await delete_record(db, owner_id, connection_id)
                    current = await db.get(CustomMcpConnection, connection_id)
                    if current is not None and current.owner_id == owner_id:
                        current.enabled = False
                        current.last_error_code = "authentication_failed"
                        current.tested_revision = None
                        current.revision += 1
                    await db.commit()
                raise OAuthError("needs_sign_in", "Sign in to this server again.", 409) from None
            raise
        async with session_factory() as db:
            await begin_write(db)
            await save_record(db, owner_id, connection_id, fresh, client_secret)
            await db.commit()
        return fresh["access_token"]
