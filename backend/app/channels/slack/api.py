"""The few Slack Web API calls the channel needs.

Two tokens: the bot token (`xoxb-`) posts and checks itself, the app-level
token (`xapp-`) only opens the Socket Mode connection. Each is sent only to
slack.com, as a bearer header; neither is ever put in an error, a log line or a
return value, so a failure can be shown to the person as it is.

Slack answers HTTP 200 with `{"ok": false, "error": "..."}` for most failures
and HTTP 429 with `Retry-After` when a method is called too fast; the rate
limit is waited out here (a few times, never long), everything else is raised
as `SlackError` with Slack's own error code.
"""

import asyncio
from collections.abc import Awaitable, Callable

import httpx

API_BASE = "https://slack.com/api"
TIMEOUT_SECONDS = 15.0
MAX_RETRY_WAIT_SECONDS = 30.0

# Errors that mean the token itself is no good: retrying cannot help.
AUTH_ERRORS = frozenset({
    "invalid_auth", "not_authed", "account_inactive", "token_revoked", "token_expired",
    "not_allowed_token_type", "org_login_required", "missing_scope", "no_permission",
})


class SlackError(Exception):
    """A Slack call failed. `code` is Slack's error code, or one of ours:
    network, bad_response, http_<status>, rate_limited, bad_socket_url."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SlackAuthError(SlackError):
    """The token was refused or lacks a permission."""


class SlackApi:
    def __init__(
        self,
        bot_token: str,
        app_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_retries: int = 3,
    ) -> None:
        self._bot_token = bot_token
        self._app_token = app_token
        self._transport = transport
        self._sleep = sleep
        self._max_retries = max_retries

    async def auth_test(self) -> dict:
        """Who the bot token belongs to: team_id, team, user_id (the bot's
        user), bot_id."""
        return await self._call("auth.test", self._bot_token, {})

    async def open_connection(self) -> str:
        """A one-time wss:// address for Socket Mode (needs the app token)."""
        data = await self._call("apps.connections.open", self._app_token, {})
        url = data.get("url")
        if not isinstance(url, str) or not url:
            raise SlackError("bad_response")
        return url

    async def post_message(self, channel: str, text: str, *, blocks: list | None = None) -> dict:
        """Post to a channel or DM. `text` is also the notification and the
        fallback when blocks are given. Links are not unfurled."""
        body: dict = {"channel": channel, "text": text, "mrkdwn": True, "unfurl_links": False, "unfurl_media": False}
        if blocks:
            body["blocks"] = blocks
        return await self._call("chat.postMessage", self._bot_token, body)

    async def update_message(self, channel: str, ts: str, text: str, *, blocks: list | None = None) -> dict:
        body: dict = {"channel": channel, "ts": ts, "text": text, "blocks": blocks or []}
        return await self._call("chat.update", self._bot_token, body)

    async def _call(self, method: str, token: str, body: dict) -> dict:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        attempt = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=self._transport, follow_redirects=False) as client:
                    response = await client.post(f"{API_BASE}/{method}", headers=headers, json=body)
            except httpx.HTTPError:
                raise SlackError("network") from None
            if response.status_code == 429:
                attempt += 1
                if attempt > self._max_retries:
                    raise SlackError("rate_limited")
                await self._sleep(_retry_after(response))
                continue
            if response.status_code >= 400:
                raise SlackError(f"http_{response.status_code}")
            try:
                data = response.json()
            except ValueError:
                raise SlackError("bad_response") from None
            if not isinstance(data, dict):
                raise SlackError("bad_response")
            if data.get("ok") is True:
                return data
            code = data.get("error")
            code = code if isinstance(code, str) and code else "unknown_error"
            raise (SlackAuthError if code in AUTH_ERRORS else SlackError)(code)


def _retry_after(response: httpx.Response) -> float:
    try:
        wait = float(response.headers.get("retry-after", "1"))
    except ValueError:
        wait = 1.0
    return min(max(wait, 0.0), MAX_RETRY_WAIT_SECONDS)
