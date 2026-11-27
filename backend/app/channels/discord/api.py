"""Discord HTTP API calls used by the DM channel; bot tokens stay in headers."""

import asyncio
from collections.abc import Awaitable, Callable
from urllib.parse import quote

import httpx

API_BASE = "https://discord.com/api/v10"


class DiscordError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DiscordAuthError(DiscordError):
    pass


class DiscordApi:
    def __init__(self, token: str, *, transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._token = token
        self._transport = transport
        self._sleep = sleep

    async def me(self) -> dict:
        return await self._call("GET", "/users/@me")

    async def gateway(self) -> str:
        data = await self._call("GET", "/gateway/bot")
        url = data.get("url")
        if not isinstance(url, str) or not url:
            raise DiscordError("bad_gateway")
        return url

    async def channel(self, channel_id: str) -> dict:
        return await self._call("GET", f"/channels/{quote(channel_id, safe='')}")

    async def post_message(self, channel: str, content: str, *,
                           components: list | None = None) -> dict:
        body: dict = {"content": content, "allowed_mentions": {"parse": []}}
        if components is not None:
            body["components"] = components
        return await self._call("POST", f"/channels/{quote(channel, safe='')}/messages", body)

    async def edit_message(self, channel: str, message_id: str, content: str,
                           *, components: list | None = None) -> dict:
        body: dict = {"content": content, "allowed_mentions": {"parse": []}, "components": components or []}
        return await self._call("PATCH", f"/channels/{quote(channel, safe='')}/messages/{quote(message_id, safe='')}", body)

    async def acknowledge(self, interaction_id: str, token: str) -> None:
        # Deferred message update (type 6) acknowledges a button without a public callback.
        await self._call("POST", f"/interactions/{quote(interaction_id, safe='')}/{quote(token, safe='')}/callback",
                         {"type": 6}, auth=False, timeout=2.5, max_retries=0)

    async def _call(self, method: str, path: str, body: dict | None = None, *,
                    auth: bool = True, timeout: float = 15, max_retries: int = 3) -> dict:
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bot {self._token}"
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=self._transport, follow_redirects=False) as client:
                    response = await client.request(method, API_BASE + path, headers=headers, json=body)
            except httpx.HTTPError:
                raise DiscordError("network") from None
            if response.status_code == 429:
                if attempt == max_retries:
                    raise DiscordError("rate_limited")
                try:
                    retry = response.json().get("retry_after", 1)
                    wait = float(retry)
                except (ValueError, TypeError, AttributeError):
                    wait = 1.0
                await self._sleep(min(max(wait, 0.0), 30.0))
                continue
            if response.status_code in (401, 403):
                raise DiscordAuthError(f"http_{response.status_code}")
            if response.status_code >= 400:
                raise DiscordError(f"http_{response.status_code}")
            if response.status_code == 204:
                return {}
            try:
                data = response.json()
            except ValueError:
                raise DiscordError("bad_response") from None
            if not isinstance(data, dict):
                raise DiscordError("bad_response")
            return data
        raise DiscordError("rate_limited")
