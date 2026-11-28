"""Owner-only Discord DM channel, using the shared channel bridge."""

import asyncio
import contextlib
import re
import secrets
import time

from app.channels.base import ChannelAdapter
from app.channels.bridge import ALREADY_ANSWERED, RECORDED, ChannelBridge
from app.channels.discord import state
from app.channels.discord.adapter import ACTION_ALLOW, ACTION_DENY, DiscordAdapter
from app.channels.discord.api import DiscordApi, DiscordAuthError, DiscordError
from app.channels.discord.events import ButtonClick, DirectMessage, parse
from app.channels.discord.gateway import GatewayClient
from app.core.config import settings
from app.core.logging import get_logger
from app.core.scheduler_lock import SchedulerLock
from app.services import agent_store
from app.services.provider_adapter import ALLOW, DENY

logger = get_logger(__name__)
LINK_WORD = re.compile(r"link\s+(\d{6})", re.IGNORECASE)
LINK_TTL_SECONDS = 600
SUPERVISE_SECONDS = 15.0
DISCORD_LOCK_KEY = 0x6F64_6469_7363_6F72


class DiscordSetupError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class LinkCodes:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._code: str | None = None
        self._expires = 0.0
        self._failures = 0

    def issue(self) -> tuple[str, int]:
        self._code = f"{secrets.randbelow(1_000_000):06d}"
        self._expires = self._clock() + LINK_TTL_SECONDS
        self._failures = 0
        return self._code, LINK_TTL_SECONDS

    @property
    def active(self) -> bool:
        return self._code is not None and self._clock() < self._expires

    def clear(self) -> None:
        self._code = None

    def redeem(self, candidate: str) -> bool:
        if not self.active:
            self._code = None
            return False
        if secrets.compare_digest(candidate, self._code or ""):
            self.clear()
            return True
        self._failures += 1
        if self._failures >= 5:
            self.clear()
        return False


class DiscordService:
    def __init__(
        self, runtime, *, api_factory=DiscordApi, client_factory=GatewayClient,
        lock: SchedulerLock | None = None, clock=time.monotonic,
        supervise_seconds: float = SUPERVISE_SECONDS,
    ) -> None:
        self._rt = runtime
        self._api_factory, self._client_factory = api_factory, client_factory
        self._lock = lock or SchedulerLock(settings.DATABASE_KIND, key=DISCORD_LOCK_KEY)
        self._codes = LinkCodes(clock)
        self._supervise_seconds = supervise_seconds
        self._apply_lock = asyncio.Lock()
        self._supervisor: asyncio.Task | None = None
        self._api = None
        self._client = None
        self._client_key: str | None = None
        self._adapter: ChannelAdapter | None = None
        self._bridge: ChannelBridge | None = None
        self._bridge_key: tuple | None = None
        self._conn_state: tuple[str, str | None] = ("not_configured", None)

    @staticmethod
    def available() -> bool:
        return not (settings.TENANT_SCHEMAS_ENABLED or settings.HOST_EXECUTION_DISABLED)

    async def start(self) -> None:
        if self.available() and self._supervisor is None:
            self._supervisor = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        task, self._supervisor = self._supervisor, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._teardown()
        with contextlib.suppress(Exception):
            await self._lock.release()

    async def _supervise(self) -> None:
        while True:
            try:
                await self.apply()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("the discord connection could not be refreshed")
            await asyncio.sleep(self._supervise_seconds)

    async def apply(self) -> None:
        async with self._apply_lock:
            config = await self._load()
            if config is None:
                await self._teardown()
                self._conn_state = ("not_configured", None)
                return
            if not await self._lock.ensure():
                await self._stop_client()
                self._conn_state = ("standby", None)
                return
            await self._ensure_client(config)
            await self._ensure_bridge(config)

    async def _ensure_client(self, config: state.DiscordConfig) -> None:
        if self._client is not None and self._client_key == config.bot_token:
            return
        await self._stop_client()
        self._api = self._api_factory(config.bot_token)
        self._client = self._client_factory(self._api, config.bot_token, self.handle_event, on_status=self._on_status)
        self._client_key = config.bot_token
        self._conn_state = ("connecting", None)
        self._client.start()

    async def _ensure_bridge(self, config: state.DiscordConfig) -> None:
        if not config.linked or config.agent_id is None or self._api is None:
            await self._stop_bridge()
            return
        key = (config.owner_user_id, config.dm_channel, config.agent_id)
        if self._bridge is not None and self._bridge_key == key:
            return
        await self._stop_bridge()
        self._adapter = DiscordAdapter(self._api, config.dm_channel)
        self._bridge = ChannelBridge(
            self._rt, self._adapter, agent_id=config.agent_id,
            load_thread_id=self._load_thread_id, save_thread_id=self._save_thread_id,
        )
        self._bridge_key = key

    async def _stop_client(self) -> None:
        client, self._client, self._client_key = self._client, None, None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.stop()

    async def _stop_bridge(self) -> None:
        bridge, self._bridge, self._bridge_key, self._adapter = self._bridge, None, None, None
        if bridge is not None:
            with contextlib.suppress(Exception):
                await bridge.close()

    async def _teardown(self) -> None:
        await self._stop_bridge()
        await self._stop_client()
        self._api = None

    def _on_status(self, status: str, detail: str | None) -> None:
        self._conn_state = (status, detail)

    async def _load(self) -> state.DiscordConfig | None:
        async with self._rt.session() as db:
            return await state.load(db, self._rt.owner_id)

    async def _save(self, config: state.DiscordConfig) -> None:
        async with self._rt.session() as db:
            await state.save(db, self._rt.owner_id, config)

    async def _load_thread_id(self) -> int | None:
        config = await self._load()
        return config.thread_id if config else None

    async def _save_thread_id(self, thread_id: int | None) -> None:
        config = await self._load()
        if config is not None:
            config.thread_id = thread_id
            await self._save(config)

    async def status(self) -> dict:
        if not self.available():
            return {"available": False, "configured": False, "state": "unavailable", "detail": None}
        config = await self._load()
        if config is None:
            return {"available": True, "configured": False, "state": "not_configured", "detail": None}
        state_name, detail = self._conn_state
        return {
            "available": True, "configured": True, "state": state_name, "detail": detail,
            "linked": config.linked, "bot": config.bot_name, "agent_id": config.agent_id,
            "link_code_active": self._codes.active,
        }

    async def connect(self, token: str) -> dict:
        self._require_available()
        token = token.strip()
        if not token or len(token) > 500 or any(ord(char) < 33 or ord(char) > 126 for char in token):
            raise DiscordSetupError("invalid_token", "Enter a Discord bot token on one line.")
        api = self._api_factory(token)
        try:
            who = await api.me()
            await api.gateway()
        except DiscordAuthError:
            raise DiscordSetupError("discord_refused_token", "Discord refused the bot token. Check it and try again.") from None
        except DiscordError as exc:
            raise DiscordSetupError("discord_unreachable", f"Could not reach Discord ({exc.code}). Try again.", 502) from None
        bot_id, bot_name = who.get("id"), who.get("username")
        if not isinstance(bot_id, str) or not bot_id.isdecimal() or not isinstance(bot_name, str):
            raise DiscordSetupError("discord_bad_answer", "Discord's answer was not what was expected.", 502)
        existing = await self._load()
        config = state.DiscordConfig(bot_token=token, bot_user_id=bot_id, bot_name=bot_name)
        if existing is not None and existing.bot_user_id == bot_id:
            config.owner_user_id, config.dm_channel = existing.owner_user_id, existing.dm_channel
            config.agent_id, config.thread_id = existing.agent_id, existing.thread_id
        elif existing is not None:
            self._codes.clear()
        if config.agent_id is None:
            async with self._rt.session() as db:
                agents = await agent_store.list_agents(db, self._rt.owner_id)
            config.agent_id = agents[0].id if agents else None
        await self._save(config)
        if self._conn_state[0] == "failed":
            await self._stop_client()
        await self.apply()
        return await self.status()

    async def disconnect(self) -> None:
        async with self._rt.session() as db:
            await state.remove(db, self._rt.owner_id)
        self._codes.clear()
        await self.apply()

    async def issue_link_code(self) -> dict:
        self._require_available()
        if await self._load() is None:
            raise DiscordSetupError("not_connected", "Connect Discord first.", 409)
        code, expires = self._codes.issue()
        return {"code": code, "expires_in": expires}

    async def set_agent(self, agent_id: int) -> dict:
        self._require_available()
        config = await self._load()
        if config is None:
            raise DiscordSetupError("not_connected", "Connect Discord first.", 409)
        async with self._rt.session() as db:
            agent = await agent_store.get_agent(db, agent_id)
        if agent is None or agent.owner_id != self._rt.owner_id or agent.archived_at is not None:
            raise DiscordSetupError("agent_unavailable", "That agent is not available.")
        config.agent_id, config.thread_id = agent_id, None
        await self._save(config)
        await self.apply()
        return await self.status()

    def _require_available(self) -> None:
        if not self.available():
            raise DiscordSetupError("not_available", "Discord is not available on a hosted workspace.", 409)

    async def handle_event(self, packet: dict) -> None:
        parsed = parse(packet)
        if parsed is None:
            return
        config = await self._load()
        if config is None:
            return
        if isinstance(parsed, DirectMessage):
            await self._on_message(config, parsed)
        else:
            await self._on_click(config, parsed)

    async def _on_message(self, config: state.DiscordConfig, message: DirectMessage) -> None:
        if message.is_bot or message.user == config.bot_user_id:
            return
        text = message.text.strip()
        link = LINK_WORD.fullmatch(text)
        if link:
            if await self._is_direct_message(message.channel) and self._codes.redeem(link.group(1)):
                await self._link(config, message)
            return
        if not config.linked or message.user != config.owner_user_id or message.channel != config.dm_channel:
            return
        if self._bridge is None:
            await self._post(config.dm_channel, "No agent is chosen for this chat yet. Choose one in Settings, Integrations.")
            return
        await self._bridge.on_owner_message(text)

    async def _is_direct_message(self, channel_id: str) -> bool:
        # A group DM also has no guild_id in Gateway events. Verify the actual
        # channel type before binding the owner and conversation to it.
        try:
            channel = await self._api.channel(channel_id)
        except DiscordError:
            return False
        return channel.get("type") == 1 and channel.get("id") == channel_id

    async def _link(self, config: state.DiscordConfig, message: DirectMessage) -> None:
        config.owner_user_id, config.dm_channel, config.thread_id = message.user, message.channel, None
        await self._save(config)
        await self._ensure_bridge(config)
        agent = None
        if config.agent_id is not None:
            async with self._rt.session() as db:
                agent = await agent_store.get_agent(db, config.agent_id)
        reply = (f"Linked. You can now talk to {agent.name} here. Send `new` to start a fresh conversation."
                 if agent is not None else "Linked. Choose an agent in Settings, Integrations to start talking.")
        await self._post(config.dm_channel, reply)

    async def _on_click(self, config: state.DiscordConfig, click: ButtonClick) -> None:
        if (not config.linked or click.user != config.owner_user_id or click.channel != config.dm_channel
                or click.message_author_id != config.bot_user_id):
            return
        decision = {ACTION_ALLOW: ALLOW, ACTION_DENY: DENY}.get(click.action)
        if decision is None:
            return
        outcome = await self._bridge.on_decision(click.approval_id, decision) if self._bridge is not None else None
        if outcome == RECORDED:
            return
        if isinstance(self._adapter, DiscordAdapter):
            title = click.message_text.removeprefix("Approval needed: ").splitlines()[0][:200] or "Approval"
            await self._adapter.replace_card(
                click.channel, click.message_id, title,
                "Already answered" if outcome == ALREADY_ANSWERED else "No longer waiting",
            )

    async def _post(self, channel: str | None, text: str) -> None:
        if self._api is None or not channel:
            return
        try:
            await self._api.post_message(channel, text)
        except DiscordError:
            logger.exception("a discord message could not be posted")


_service: DiscordService | None = None


def get_service() -> DiscordService:
    global _service
    if _service is None:
        from app.services.thread_runtime import runtime
        _service = DiscordService(runtime)
    return _service
