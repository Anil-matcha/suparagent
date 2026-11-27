"""Desktop account session proxy and profile-bound session storage.

The renderer talks only to the local API. Cloud credentials are kept encrypted in
the profile and are never included in a renderer response.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import asyncio
import base64
import hashlib
import secrets
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, MultiFernet, InvalidToken

from app.desktop import profile
from app.desktop.loopback import LoopbackListener

CLOUD_DESKTOP_URL = "https://suparagent.ai/api/v1/accounts/desktop"
SESSION_FILE = "account-session.enc"
BINDING_FILE = "account-binding.enc"
SESSION_CACHE_SECONDS = 60
GOOGLE_WAIT_SECONDS = 600  # the account service keeps a sign-in flow for 10 minutes
GOOGLE_RETURN_ERRORS = {"signup_closed": "google_signup_closed"}


class DesktopAccountError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


class AccountManager:
    def __init__(self, *, client: httpx.AsyncClient | None = None, base_url: str = CLOUD_DESKTOP_URL):
        current = profile.current()
        if current is None:
            raise RuntimeError("Desktop profile is not active.")
        self.data_dir = current.data_dir
        self._cipher = MultiFernet([
            Fernet(key.strip().encode("ascii"))
            for key in current.encryption_key.split(",") if key.strip()
        ])
        self._base_url = base_url
        self._client = client
        self._cache_until = 0.0
        self._cached_user: dict[str, str] | None = None
        self._account_lock = asyncio.Lock()
        self._google_wait: asyncio.Future | None = None

    @property
    def _session_path(self) -> Path:
        return self.data_dir / SESSION_FILE

    @property
    def _binding_path(self) -> Path:
        return self.data_dir / BINDING_FILE

    def _read_secret(self, path: Path) -> dict[str, Any] | None:
        try:
            payload = self._cipher.decrypt(path.read_bytes())
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError("invalid encrypted profile record")
            return value
        except FileNotFoundError:
            return None
        except (InvalidToken, ValueError, OSError, json.JSONDecodeError):
            raise DesktopAccountError(503, "profile_storage_error") from None

    def _write_secret(self, path: Path, value: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        encrypted = self._cipher.encrypt(json.dumps(value, separators=(",", ":")).encode())
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.data_dir)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
            os.chmod(path, 0o600)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def _clear_session(self) -> None:
        try:
            self._session_path.unlink()
        except FileNotFoundError:
            pass
        self._cached_user = None
        self._cache_until = 0
        _wake_event_stream()

    def _token(self) -> str | None:
        stored = self._read_secret(self._session_path)
        token = stored.get("token") if stored else None
        if stored and not isinstance(stored.get("expires_at"), str):
            raise DesktopAccountError(503, "profile_storage_error")
        return token if isinstance(token, str) and token else None

    @staticmethod
    def _expiry_epoch(value: str) -> float:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            raise DesktopAccountError(503, "profile_storage_error") from None

    async def _request(self, method: str, suffix: str = "", *, token: str | None = None,
                       json_body: dict[str, Any] | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"{self._base_url}{suffix}"
        if self._client is not None:
            return await self._client.request(method, url, headers=headers, json=json_body, follow_redirects=False)
        async with httpx.AsyncClient(timeout=10.0, verify=True, follow_redirects=False) as client:
            return await client.request(method, url, headers=headers, json=json_body)

    @staticmethod
    def _remote_error(response: httpx.Response) -> DesktopAccountError:
        # Never forward remote detail bodies (they may contain private data).
        if response.status_code == 429:
            return DesktopAccountError(429, "rate_limited")
        if response.status_code in (400, 401, 403, 409, 422):
            return DesktopAccountError(response.status_code, "account_request_rejected")
        return DesktopAccountError(503, "account_service_unavailable")

    def relay_credentials(self) -> tuple[str, str] | None:
        """(the event relay's address in the account service, the session token) for the new-mail relay, or None when
        nobody is signed in or the session has run out. Reads the encrypted session only: no network call, so the
        caller can ask as often as it likes. The token never leaves the backend."""
        try:
            stored = self._read_secret(self._session_path)
            token = stored.get("token") if stored else None
            if not (isinstance(token, str) and token) or self._expiry_epoch(stored["expires_at"]) <= time.time():
                return None
        except DesktopAccountError:
            return None
        base = self._base_url.rstrip("/")
        return (base.rsplit("/desktop", 1)[0] if base.endswith("/desktop") else base) + "/relay", token

    def profile_bound(self) -> bool:
        binding = self._read_secret(self._binding_path)
        return bool(binding and isinstance(binding.get("tenant_id"), str))

    async def status(self) -> dict[str, Any]:
        token = self._token()
        if not token:
            return {"signed_in": False, "email": None, "tenantId": None,
                    "account_required": True, "profile_bound": self.profile_bound()}
        if self._cached_user and time.monotonic() < self._cache_until:
            return {"signed_in": True, **self._cached_user, "account_required": True,
                    "profile_bound": self.profile_bound()}
        stored = self._read_secret(self._session_path)
        if stored and self._expiry_epoch(stored["expires_at"]) <= time.time():
            self._clear_session()
            return {"signed_in": False, "email": None, "tenantId": None,
                    "account_required": True, "profile_bound": self.profile_bound()}
        try:
            response = await self._request("GET", "/session", token=token)
        except httpx.HTTPError:
            raise DesktopAccountError(503, "account_service_unavailable") from None
        if response.status_code == 401:
            self._clear_session()
            return {"signed_in": False, "email": None, "tenantId": None,
                    "account_required": True, "profile_bound": self.profile_bound()}
        if response.status_code != 200:
            raise DesktopAccountError(503, "account_service_unavailable")
        try:
            user = response.json()["user"]
            email, tenant_id = user["email"], user["tenantId"]
        except (ValueError, KeyError, TypeError):
            raise DesktopAccountError(503, "account_service_unavailable") from None
        if (not isinstance(email, str) or "@" not in email or len(email) > 254
                or not isinstance(tenant_id, str) or not tenant_id or len(tenant_id) > 64):
            raise DesktopAccountError(503, "account_service_unavailable")
        binding = self._read_secret(self._binding_path)
        if binding and binding.get("tenant_id") != tenant_id:
            self._clear_session()
            return {"signed_in": False, "email": None, "tenantId": None,
                    "account_required": True, "profile_bound": True}
        if not binding:
            self._write_secret(self._binding_path, {"tenant_id": tenant_id, "email": email})
        user_name = user.get("name") if isinstance(user.get("name"), str) else None
        user_avatar = (
            user.get("avatarUrl")
            if isinstance(user.get("avatarUrl"), str)
            else (user.get("avatar_url") if isinstance(user.get("avatar_url"), str) else None)
        )
        self._cached_user = {
            "email": email,
            "tenantId": tenant_id,
            "name": user_name,
            "avatar_url": user_avatar,
        }
        session = self._read_secret(self._session_path)
        expires_epoch = self._expiry_epoch(session["expires_at"]) if session else time.time()
        self._cache_until = time.monotonic() + min(SESSION_CACHE_SECONDS, max(0, expires_epoch - time.time()))
        return {"signed_in": True, **self._cached_user, "account_required": True,
                "profile_bound": self.profile_bound()}

    async def email_start(self, email: str, invite_code: str | None = None) -> dict[str, str]:
        body = {"email": email}
        if invite_code is not None:
            body["invite_code"] = invite_code
        try:
            response = await self._request("POST", "/email/start", json_body=body)
        except httpx.HTTPError:
            raise DesktopAccountError(503, "account_service_unavailable") from None
        if response.status_code not in (200, 202):
            raise self._remote_error(response)
        return {"status": "if_account_exists_code_sent"}

    async def email_verify(self, email: str, code: str, adopt_local_data: bool) -> dict[str, Any]:
        async with self._account_lock:
            binding = self._read_secret(self._binding_path)
            if not isinstance(adopt_local_data, bool):
                raise DesktopAccountError(400, "invalid_request")
            if not binding and not adopt_local_data:
                raise DesktopAccountError(400, "local_data_adoption_required")
            try:
                response = await self._request("POST", "/email/verify", json_body={"email": email, "code": code})
            except httpx.HTTPError:
                raise DesktopAccountError(503, "account_service_unavailable") from None
            if response.status_code not in (200, 201):
                raise self._remote_error(response)
            return await self._complete(response, binding)

    async def _complete(self, response: httpx.Response, binding: dict[str, Any] | None) -> dict[str, Any]:
        """Check a session the account service issued, bind it to this profile, and keep it encrypted."""
        try:
            payload = response.json()
            token = payload["token"]
            user = payload["user"]
            email_value, tenant_id = user["email"], user["tenantId"]
            expires_at = payload["expires_at"]
            if not all(isinstance(v, str) and v for v in (token, email_value, tenant_id, expires_at)):
                raise ValueError
            expires_epoch = self._expiry_epoch(expires_at)
            if expires_epoch <= time.time() or "@" not in email_value or len(email_value) > 254 or len(tenant_id) > 64:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise DesktopAccountError(503, "account_service_unavailable") from None
        if binding and binding.get("tenant_id") != tenant_id:
            try:
                await self._request("POST", "/logout", token=token)
            except httpx.HTTPError:
                pass
            raise DesktopAccountError(409, "profile_account_mismatch")
        # Bind first: a crash after this write must never leave adopted local data
        # available to an unbound account on the next launch.
        if not binding:
            self._write_secret(self._binding_path, {"tenant_id": tenant_id, "email": email_value})
        self._write_secret(self._session_path, {"token": token, "expires_at": expires_at})
        _wake_event_stream()
        user_name = user.get("name") if isinstance(user.get("name"), str) else None
        user_avatar = (
            user.get("avatarUrl")
            if isinstance(user.get("avatarUrl"), str)
            else (user.get("avatar_url") if isinstance(user.get("avatar_url"), str) else None)
        )
        self._cached_user = {
            "email": email_value,
            "tenantId": tenant_id,
            "name": user_name,
            "avatar_url": user_avatar,
        }
        self._cache_until = time.monotonic() + min(SESSION_CACHE_SECONDS, expires_epoch - time.time())
        return {"signed_in": True, "email": email_value, "tenantId": tenant_id,
                "account_required": True, "profile_bound": True}

    def _accounts_config_url(self) -> str:
        return self._base_url.rsplit("/desktop", 1)[0] + "/config"

    async def _google_available(self) -> bool:
        try:
            config = (await self._request_url("GET", self._accounts_config_url())).json()
        except (httpx.HTTPError, ValueError):
            raise DesktopAccountError(503, "account_service_unavailable") from None
        return isinstance(config, dict) and config.get("googleSignIn") is True

    async def _request_url(self, method: str, url: str) -> httpx.Response:
        if self._client is not None:
            return await self._client.request(method, url, follow_redirects=False)
        async with httpx.AsyncClient(timeout=10.0, verify=True, follow_redirects=False) as client:
            return await client.request(method, url)

    def google_cancel(self) -> bool:
        """End a Google sign-in that is waiting for the browser. True when one was waiting."""
        wait = self._google_wait
        if wait is None or wait.done():
            return False
        wait.set_exception(DesktopAccountError(409, "google_sign_in_cancelled"))
        return True

    async def google_sign_in(self, adopt_local_data: bool, *, open_browser: Callable[[str], Any] | None = None,
                             wait_seconds: float = GOOGLE_WAIT_SECONDS) -> dict[str, Any]:
        """Sign in with Google in the person's own browser.

        The account service sends the browser back to a listener on this computer with a one-time grant.
        The grant is worth nothing without `verifier`, which never leaves this process, so the result can
        only land here. Google refuses embedded browser windows, which is why the system browser is used."""
        if not isinstance(adopt_local_data, bool):
            raise DesktopAccountError(400, "invalid_request")
        if not self._read_secret(self._binding_path) and not adopt_local_data:
            raise DesktopAccountError(400, "local_data_adoption_required")
        if not await self._google_available():
            raise DesktopAccountError(503, "google_sign_in_unavailable")
        self.google_cancel()  # a new sign-in replaces one still waiting
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        nonce = secrets.token_urlsafe(18)
        loop = asyncio.get_running_loop()
        wait: asyncio.Future = loop.create_future()

        def receive(params: dict[str, str]) -> tuple[int, str]:
            if params.get("nonce") != nonce:
                return 400, "This page is not part of a sign-in."
            if wait.done():
                return 400, "This sign-in has ended. Return to Suparagent."
            if params.get("error") or not params.get("grant"):
                wait.set_result({"error": params.get("error") or "failed"})
                return 400, "Sign-in did not finish. Return to Suparagent."
            wait.set_result({"grant": params["grant"]})
            return 200, "You are signed in. You can close this tab and return to Suparagent."

        listener = LoopbackListener("/callback", receive)
        await listener.start()
        self._google_wait = wait
        try:
            url = f"{self._base_url}/google/start?" + urlencode({"port": listener.port, "challenge": challenge, "nonce": nonce})
            opened = await asyncio.to_thread(open_browser or webbrowser.open, url)
            if opened is False:
                raise DesktopAccountError(503, "browser_unavailable")
            try:
                result = await asyncio.wait_for(asyncio.shield(wait), wait_seconds)
            except asyncio.TimeoutError:
                raise DesktopAccountError(408, "google_sign_in_timeout") from None
        finally:
            self._google_wait = None
            if not wait.done():
                wait.cancel()
            await listener.close()
        if "error" in result:
            raise DesktopAccountError(400, GOOGLE_RETURN_ERRORS.get(result["error"], "google_sign_in_failed"))
        async with self._account_lock:
            binding = self._read_secret(self._binding_path)
            try:
                response = await self._request("POST", "/google/exchange", json_body={"grant": result["grant"], "verifier": verifier})
            except httpx.HTTPError:
                raise DesktopAccountError(503, "account_service_unavailable") from None
            if response.status_code not in (200, 201):
                raise self._remote_error(response)
            return await self._complete(response, binding)

    async def logout(self) -> dict[str, Any]:
        async with self._account_lock:
            token = self._token()
            remote_signed_out = True
            if token:
                try:
                    response = await self._request("POST", "/logout", token=token)
                    remote_signed_out = response.status_code in (200, 204, 401)
                except httpx.HTTPError:
                    remote_signed_out = False
            self._clear_session()
            return {"signed_in": False, "remote_signed_out": remote_signed_out,
                    "profile_bound": self.profile_bound()}

    def _clear_binding(self) -> None:
        try:
            self._binding_path.unlink()
        except FileNotFoundError:
            pass

    async def reset_profile(self, wipe) -> dict[str, Any]:
        """Erase this profile's local data, then unbind it from its account.

        Data goes first: a crash in between leaves an empty profile still bound to the
        old account, never old data open to a new account. Sign-out never calls this.
        """
        async with self._account_lock:
            token = self._token()
            try:
                await wipe()
            except Exception:
                raise DesktopAccountError(500, "local_reset_failed") from None
            self._clear_binding()
            if token:
                try:
                    await self._request("POST", "/logout", token=token)
                except httpx.HTTPError:
                    pass
            self._clear_session()
            return {"signed_in": False, "email": None, "tenantId": None,
                    "account_required": True, "profile_bound": False}

    async def delete_account(self, wipe) -> dict[str, Any]:
        """Delete the cloud account, then reset the profile. Fails closed: if the
        cloud call does not succeed, nothing local is changed or reported as deleted."""
        async with self._account_lock:
            token = self._token()
            if not token:
                raise DesktopAccountError(401, "account_sign_in_required")
            try:
                binding = self._read_secret(self._binding_path)
                email = binding.get("email") if binding else None
                if not isinstance(email, str) or not email:
                    raise DesktopAccountError(401, "account_sign_in_required")
                response = await self._request("DELETE", "/account", token=token, json_body={"confirm_email": email})
            except httpx.HTTPError:
                raise DesktopAccountError(503, "account_service_unavailable") from None
            if response.status_code == 401:
                self._clear_session()
                raise DesktopAccountError(401, "account_sign_in_required")
            if response.status_code not in (200, 204):
                raise self._remote_error(response)
            self._clear_session()  # the cloud revoked the token with the account
            try:
                await wipe()
            except Exception:
                # The account is gone; the binding stays so local data is not opened by
                # another account. Resetting the profile finishes the cleanup.
                raise DesktopAccountError(500, "account_deleted_local_cleanup_failed") from None
            self._clear_binding()
            return {"signed_in": False, "email": None, "tenantId": None,
                    "account_required": True, "profile_bound": False}

    async def require_account(self) -> dict[str, str]:
        state = await self.status()
        if not state["signed_in"]:
            raise DesktopAccountError(401, "account_sign_in_required")
        return {"email": state["email"], "tenantId": state["tenantId"]}


def _wake_event_stream() -> None:
    """A new sign-in may let the new-mail relay connect (services/event_stream.py). Never lets that fail a sign-in."""
    try:
        from app.services import event_stream
        event_stream.poke()
    except Exception:  # noqa: BLE001
        pass


_manager: AccountManager | None = None


def get_manager() -> AccountManager:
    global _manager
    if _manager is None:
        _manager = AccountManager()
    return _manager
