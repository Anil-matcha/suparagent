"""Desktop account proxy tests: no live cloud calls are made."""

import json
import sys

import httpx
import pytest
from cryptography.fernet import Fernet

from app.desktop import account, profile


@pytest.fixture
def manager(tmp_path):
    profile._current = profile.DesktopProfile(tmp_path, "http://127.0.0.1:1234", Fernet.generate_key().decode())
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    client = httpx.AsyncClient(transport=transport)
    instance = account.AccountManager(client=client)
    yield instance, client
    profile._current = None


@pytest.mark.asyncio
async def test_verified_session_is_encrypted_restored_and_never_returned(manager):
    instance, client = manager
    async def handler(request):
        assert request.url.host == "suparagent.ai"
        assert request.url.path.endswith("/email/verify")
        return httpx.Response(200, json={"token": "secret-device-token", "expires_at": "2030-01-01T00:00:00Z",
                                         "user": {"email": "person@example.org", "tenantId": "tenant-1"}})
    client._transport = httpx.MockTransport(handler)
    result = await instance.email_verify("person@example.org", "123456", True)
    assert "secret-device-token" not in json.dumps(result)
    raw = (instance.data_dir / account.SESSION_FILE).read_bytes()
    assert b"secret-device-token" not in raw
    if sys.platform != "win32":
        assert (instance.data_dir / account.SESSION_FILE).stat().st_mode & 0o777 == 0o600

    async def session_handler(request):
        assert request.headers["authorization"] == "Bearer secret-device-token"
        return httpx.Response(200, json={"user": {"email": "person@example.org", "tenantId": "tenant-1"}})
    client._transport = httpx.MockTransport(session_handler)
    profile._current = profile.DesktopProfile(instance.data_dir, "http://127.0.0.1:1234", profile._current.encryption_key)
    restored = account.AccountManager(client=client)
    assert (await restored.status())["signed_in"] is True


@pytest.mark.asyncio
async def test_tenant_mismatch_clears_session_and_rejects_binding(manager):
    instance, client = manager
    instance._write_secret(instance._binding_path, {"tenant_id": "tenant-old", "email": "old@example.org"})
    instance._write_secret(instance._session_path, {"token": "device-token", "expires_at": "2099-01-01T00:00:00Z"})
    client._transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "user": {"email": "new@example.org", "tenantId": "tenant-new"}}))
    result = await instance.status()
    assert result["signed_in"] is False
    assert instance._token() is None
    assert instance._read_secret(instance._binding_path)["tenant_id"] == "tenant-old"


@pytest.mark.asyncio
async def test_bound_profile_can_reauthenticate_same_tenant_without_new_adoption(manager):
    instance, client = manager
    instance._write_secret(instance._binding_path, {"tenant_id": "tenant-1", "email": "old@example.org"})
    client._transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "token": "same-tenant-token", "expires_at": "2030-01-01T00:00:00Z",
        "user": {"email": "new@example.org", "tenantId": "tenant-1"}}))
    result = await instance.email_verify("new@example.org", "123456", False)
    assert result["signed_in"] is True
    assert instance._read_secret(instance._binding_path)["tenant_id"] == "tenant-1"


@pytest.mark.asyncio
async def test_new_session_for_different_tenant_is_revoked_and_not_stored(manager):
    instance, client = manager
    instance._write_secret(instance._binding_path, {"tenant_id": "tenant-old", "email": "old@example.org"})
    revoked = []
    async def handler(request):
        if request.url.path.endswith("/email/verify"):
            return httpx.Response(200, json={"token": "wrong-tenant-token", "expires_at": "2030-01-01T00:00:00Z",
                                             "user": {"email": "new@example.org", "tenantId": "tenant-new"}})
        revoked.append(request.headers.get("authorization"))
        return httpx.Response(200)
    client._transport = httpx.MockTransport(handler)
    with pytest.raises(account.DesktopAccountError) as exc:
        await instance.email_verify("new@example.org", "123456", False)
    assert exc.value.code == "profile_account_mismatch"
    assert revoked == ["Bearer wrong-tenant-token"]
    assert instance._read_secret(instance._binding_path)["tenant_id"] == "tenant-old"
    assert instance._token() is None


@pytest.mark.asyncio
async def test_corrupt_binding_fails_closed_instead_of_being_replaced(manager):
    instance, client = manager
    instance._binding_path.write_bytes(b"not an encrypted profile record")
    client._transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "token": "secret", "expires_at": "2030-01-01T00:00:00Z",
        "user": {"email": "person@example.org", "tenantId": "tenant-1"}}))
    with pytest.raises(account.DesktopAccountError) as exc:
        await instance.email_verify("person@example.org", "123456", True)
    assert exc.value.code == "profile_storage_error"
    assert instance._binding_path.read_bytes() == b"not an encrypted profile record"
    assert not instance._session_path.exists()


@pytest.mark.asyncio
async def test_verify_requires_explicit_adoption_and_server_errors_are_redacted(manager):
    instance, client = manager
    with pytest.raises(account.DesktopAccountError) as exc:
        await instance.email_verify("p@example.org", "123456", False)
    assert exc.value.code == "local_data_adoption_required"
    client._transport = httpx.MockTransport(lambda request: httpx.Response(500, text="private stack trace"))
    with pytest.raises(account.DesktopAccountError) as exc:
        await instance.email_start("p@example.org")
    assert exc.value.code == "account_service_unavailable"
    assert "private stack trace" not in str(exc.value)


@pytest.mark.asyncio
async def test_remote_unauthorized_removes_saved_session(manager):
    instance, client = manager
    instance._write_secret(instance._session_path, {"token": "device-token", "expires_at": "2099-01-01T00:00:00Z"})
    client._transport = httpx.MockTransport(lambda request: httpx.Response(401))
    result = await instance.status()
    assert result["signed_in"] is False
    assert instance._token() is None


# --- Google sign-in (system browser + loopback return) ---------------------

import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit


def _google_cloud(client, seen, *, available=True, status=200):
    """A fake account service: config, and an exchange that insists on the PKCE verifier."""
    def handler(request):
        if request.url.path.endswith("/accounts/config"):
            return httpx.Response(200, json={"googleSignIn": available})
        assert request.url.path.endswith("/google/exchange")
        body = json.loads(request.content)
        seen.append(body)
        digest = base64.urlsafe_b64encode(hashlib.sha256(body["verifier"].encode()).digest()).decode().rstrip("=")
        assert digest == seen[0]["challenge"], "the verifier does not match the challenge sent to the browser"
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"token": "google-device-token", "expires_at": "2030-01-01T00:00:00Z",
                                         "user": {"email": "g@example.org", "tenantId": "tenant-g"}})
    client._transport = httpx.MockTransport(handler)


def _browser(seen, *returns):
    """A fake system browser. Each entry of `returns` is (query to send back, extra headers)."""
    loop = asyncio.get_running_loop()  # built inside the test; open_browser itself runs in a worker thread

    def open_browser(url):
        query = parse_qs(urlsplit(url).query)
        port, nonce = int(query["port"][0]), query["nonce"][0]
        seen.insert(0, {"challenge": query["challenge"][0]})

        async def come_back():
            await asyncio.sleep(0.05)
            async with httpx.AsyncClient() as http:
                for params, headers in returns:
                    q = {"nonce": nonce, **params} if params.get("nonce") != "WRONG" else params
                    await http.get(f"http://127.0.0.1:{port}/callback", params=q, headers=headers)
        loop.call_soon_threadsafe(lambda: loop.create_task(come_back()))
        return True
    return open_browser


@pytest.mark.asyncio
async def test_google_sign_in_returns_through_loopback_and_stores_an_encrypted_session(manager):
    instance, client = manager
    seen: list = []
    _google_cloud(client, seen)
    result = await instance.google_sign_in(True, open_browser=_browser(seen, ({"grant": "one-time-grant"}, {})), wait_seconds=5)
    assert result["signed_in"] is True and result["email"] == "g@example.org"
    assert seen[1]["grant"] == "one-time-grant"
    assert b"google-device-token" not in (instance.data_dir / account.SESSION_FILE).read_bytes()
    assert instance._read_secret(instance._binding_path)["tenant_id"] == "tenant-g"


@pytest.mark.asyncio
async def test_google_sign_in_ignores_wrong_nonce_and_rebinding_hosts_then_accepts_the_real_return(manager):
    instance, client = manager
    seen: list = []
    _google_cloud(client, seen)
    browser = _browser(seen,
                       ({"grant": "forged", "nonce": "WRONG"}, {}),                 # another page guessing the port
                       ({"grant": "forged"}, {"Host": "evil.example"}),             # a name rebound to 127.0.0.1
                       ({"grant": "the-real-grant"}, {}))
    await instance.google_sign_in(True, open_browser=browser, wait_seconds=5)
    assert seen[1]["grant"] == "the-real-grant"


@pytest.mark.asyncio
async def test_google_sign_in_failures_are_redacted_and_never_sign_in(manager):
    instance, client = manager
    seen: list = []
    _google_cloud(client, seen)
    with pytest.raises(account.DesktopAccountError) as closed:
        await instance.google_sign_in(True, open_browser=_browser(seen, ({"error": "signup_closed"}, {})), wait_seconds=5)
    assert closed.value.code == "google_signup_closed"
    with pytest.raises(account.DesktopAccountError) as failed:
        await instance.google_sign_in(True, open_browser=_browser(seen, ({"error": "cancelled"}, {})), wait_seconds=5)
    assert failed.value.code == "google_sign_in_failed"
    assert instance._token() is None


@pytest.mark.asyncio
async def test_google_sign_in_guards_adoption_availability_browser_and_timeout(manager):
    instance, client = manager
    seen: list = []
    _google_cloud(client, seen)
    with pytest.raises(account.DesktopAccountError) as e:
        await instance.google_sign_in(False, open_browser=lambda url: True)
    assert e.value.code == "local_data_adoption_required"
    with pytest.raises(account.DesktopAccountError) as e:
        await instance.google_sign_in(True, open_browser=lambda url: False)
    assert e.value.code == "browser_unavailable"
    with pytest.raises(account.DesktopAccountError) as e:
        await instance.google_sign_in(True, open_browser=lambda url: True, wait_seconds=0.2)
    assert e.value.code == "google_sign_in_timeout" and instance._google_wait is None
    _google_cloud(client, seen, available=False)
    with pytest.raises(account.DesktopAccountError) as e:
        await instance.google_sign_in(True, open_browser=lambda url: True)
    assert e.value.code == "google_sign_in_unavailable"


@pytest.mark.asyncio
async def test_google_sign_in_can_be_cancelled_while_waiting_and_a_rejected_exchange_is_redacted(manager):
    instance, client = manager
    seen: list = []
    _google_cloud(client, seen)
    task = asyncio.create_task(instance.google_sign_in(True, open_browser=lambda url: True, wait_seconds=30))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if instance._google_wait is not None:
            break
    assert instance.google_cancel() is True
    with pytest.raises(account.DesktopAccountError) as e:
        await task
    assert e.value.code == "google_sign_in_cancelled" and instance.google_cancel() is False
    _google_cloud(client, seen, status=401)
    with pytest.raises(account.DesktopAccountError) as rejected:
        await instance.google_sign_in(True, open_browser=_browser(seen, ({"grant": "stale"}, {})), wait_seconds=5)
    assert rejected.value.code == "account_request_rejected"
    assert instance._token() is None
