"""The Slack channel as a running part of the backend.

Owns the saved connection (state.py), the Socket Mode client, the adapter and
the bridge, and decides what each incoming Slack event is allowed to do. The
rules, in the order they are applied to a direct message:

1. Not from the connected workspace, from a bot, an edit or other non-plain
   message, or from the app's own user: ignored.
2. `link 123456` with a live code made in Settings: the sender becomes the
   owner and the DM becomes the chat. A wrong or expired code is ignored, with
   no answer, so the app never says whether a code exists. Five wrong tries
   cancel the code.
3. Before anyone is linked, everything else is ignored.
4. After linking, only a message from the owner's own user id in the owner's
   own DM goes on; any other sender or place is ignored.

A button click is held to the same owner rules. Slack tokens are read from the
encrypted credential and kept only in memory objects of this class; nothing
here returns or logs one.

One connection per database: the connection is opened only by the backend that
holds an advisory lock (PostgreSQL; on SQLite the data-folder lease already
means one backend), and a backend that loses the lock closes it.
"""

import asyncio
import contextlib
import re
import secrets
import time

from app.channels.base import ChannelAdapter
from app.channels.bridge import ALREADY_ANSWERED, RECORDED, ChannelBridge
from app.channels.slack import state
from app.channels.slack.adapter import ACTION_ALLOW, ACTION_DENY, SlackAdapter
from app.channels.slack.api import SlackApi, SlackAuthError, SlackError
from app.channels.slack.events import ButtonClick, DirectMessage, parse
from app.channels.slack.socket import SocketModeClient
from app.core.config import settings
from app.core.logging import get_logger
from app.core.scheduler_lock import SchedulerLock
from app.services import agent_store
from app.services.provider_adapter import ALLOW, DENY

logger = get_logger(__name__)

LINK_WORD = re.compile(r"link\s+(\d{6})", re.IGNORECASE)
LINK_TTL_SECONDS = 600
MAX_LINK_FAILURES = 5
SUPERVISE_SECONDS = 15.0
MAX_TOKEN_CHARS = 500
# One fixed key for "the Slack connection of this database" (the bytes of "odslack").
SLACK_LOCK_KEY = 0x6F64_736C_6163_6B00


class SlackSetupError(Exception):
    """What the person did could not be done; `message` is safe to show."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class LinkCodes:
    """The one-time code Settings shows, kept in memory only."""

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
        if secrets.compare_digest(candidate.encode(), (self._code or "").encode()):
            self._code = None  # single use
            return True
        self._failures += 1
        if self._failures >= MAX_LINK_FAILURES:
            self._code = None
        return False


class SlackService:
    def __init__(
        self,
        runtime,
        *,
        api_factory=SlackApi,
        client_factory=SocketModeClient,
        lock: SchedulerLock | None = None,
        clock=time.monotonic,
        supervise_seconds: float = SUPERVISE_SECONDS,
    ) -> None:
        self._rt = runtime
        self._api_factory = api_factory
        self._client_factory = client_factory
        self._lock = lock or SchedulerLock(settings.DATABASE_KIND, key=SLACK_LOCK_KEY)
        self._supervise_seconds = supervise_seconds
        self._codes = LinkCodes(clock)
        self._apply_lock = asyncio.Lock()
        self._supervisor: asyncio.Task | None = None
        self._api = None
        self._client = None
        self._client_key: tuple | None = None
        self._adapter: ChannelAdapter | None = None
        self._bridge: ChannelBridge | None = None
        self._bridge_key: tuple | None = None
        self._conn_state: tuple[str, str | None] = ("not_configured", None)

    # -- lifecycle --------------------------------------------------------------------

    @staticmethod
    def available() -> bool:
        """Not offered on a hosted deployment, which never runs a person's own chat bridge."""
        return not (settings.TENANT_SCHEMAS_ENABLED or settings.HOST_EXECUTION_DISABLED)

    async def start(self) -> None:
        """Begin keeping the connection up. Never raises: a Slack problem must
        not stop the app from starting."""
        if not self.available() or self._supervisor is not None:
            return
        self._supervisor = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        supervisor, self._supervisor = self._supervisor, None
        if supervisor is not None:
            supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await supervisor
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
                logger.exception("the slack connection could not be refreshed")
            await asyncio.sleep(self._supervise_seconds)

    async def apply(self) -> None:
        """Make what is running match what is saved."""
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

    async def _ensure_client(self, config: state.SlackConfig) -> None:
        key = (config.bot_token, config.app_token)
        if self._client is not None and self._client_key == key:
            return
        await self._stop_client()
        self._api = self._api_factory(config.bot_token, config.app_token)
        self._client = self._client_factory(self._api, self.handle_envelope, on_status=self._on_status)
        self._client_key = key
        self._conn_state = ("connecting", None)
        self._client.start()

    async def _ensure_bridge(self, config: state.SlackConfig) -> None:
        if not config.linked or config.agent_id is None or self._api is None:
            await self._stop_bridge()
            return
        key = (config.owner_user_id, config.dm_channel, config.agent_id)
        if self._bridge is not None and self._bridge_key == key:
            return
        await self._stop_bridge()
        self._adapter = SlackAdapter(self._api, config.dm_channel)
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

    # -- saved state ------------------------------------------------------------------

    async def _load(self) -> state.SlackConfig | None:
        async with self._rt.session() as db:
            return await state.load(db, self._rt.owner_id)

    async def _save(self, config: state.SlackConfig) -> None:
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

    # -- what Settings does -----------------------------------------------------------

    async def status(self) -> dict:
        if not self.available():
            return {"available": False, "configured": False, "state": "unavailable", "detail": None}
        config = await self._load()
        if config is None:
            return {"available": True, "configured": False, "state": "not_configured", "detail": None}
        status, detail = self._conn_state
        return {
            "available": True, "configured": True, "state": status, "detail": detail,
            "linked": config.linked, "team": config.team_name, "agent_id": config.agent_id,
            "link_code_active": self._codes.active,
        }

    async def connect(self, bot_token: str, app_token: str) -> dict:
        self._require_available()
        bot, app = bot_token.strip(), app_token.strip()
        _check_tokens(bot, app)
        api = self._api_factory(bot, app)
        try:
            who = await api.auth_test()
            await api.open_connection()  # proves the app-level token before anything is saved
        except SlackAuthError as exc:
            raise SlackSetupError("slack_refused_token", f"Slack refused the tokens ({exc.code}). Check both and try again.") from None
        except SlackError as exc:
            raise SlackSetupError("slack_unreachable", f"Could not reach Slack ({exc.code}). Try again.", 502) from None
        team_id, bot_user = who.get("team_id"), who.get("user_id")
        if not isinstance(team_id, str) or not isinstance(bot_user, str):
            raise SlackSetupError("slack_bad_answer", "Slack's answer was not what was expected. Try again.", 502)
        existing = await self._load()
        same_app = existing is not None and existing.team_id == team_id and existing.bot_user_id == bot_user
        config = state.SlackConfig(
            bot_token=bot, app_token=app, team_id=team_id,
            team_name=who.get("team") if isinstance(who.get("team"), str) else team_id,
            bot_user_id=bot_user, bot_id=who.get("bot_id") if isinstance(who.get("bot_id"), str) else None,
        )
        if same_app:
            config.owner_user_id, config.dm_channel = existing.owner_user_id, existing.dm_channel
            config.agent_id, config.thread_id = existing.agent_id, existing.thread_id
        if config.agent_id is None:
            async with self._rt.session() as db:
                agents = await agent_store.list_agents(db, self._rt.owner_id)
            config.agent_id = agents[0].id if agents else None
        await self._save(config)
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
            raise SlackSetupError("not_connected", "Connect Slack first.", 409)
        code, expires = self._codes.issue()
        return {"code": code, "expires_in": expires}

    async def set_agent(self, agent_id: int) -> dict:
        self._require_available()
        config = await self._load()
        if config is None:
            raise SlackSetupError("not_connected", "Connect Slack first.", 409)
        async with self._rt.session() as db:
            agent = await agent_store.get_agent(db, agent_id)
        if agent is None or agent.owner_id != self._rt.owner_id or agent.archived_at is not None:
            raise SlackSetupError("agent_unavailable", "That agent is not available.")
        config.agent_id, config.thread_id = agent_id, None
        await self._save(config)
        await self.apply()
        return await self.status()

    def _require_available(self) -> None:
        if not self.available():
            raise SlackSetupError("not_available", "Slack is not available on a hosted workspace.", 409)

    # -- what Slack sends -------------------------------------------------------------

    async def handle_envelope(self, envelope: dict) -> None:
        parsed = parse(envelope)
        if parsed is None:
            return
        config = await self._load()
        if config is None:
            return
        if isinstance(parsed, DirectMessage):
            await self._on_message(config, parsed)
        elif isinstance(parsed, ButtonClick):
            await self._on_click(config, parsed)

    async def _on_message(self, config: state.SlackConfig, message: DirectMessage) -> None:
        if (message.team and message.team != config.team_id) or message.subtype or message.bot_id:
            return
        if message.user == config.bot_user_id:
            return
        text = message.text.strip()
        link = LINK_WORD.fullmatch(text)
        if link:
            if self._codes.redeem(link.group(1)):
                await self._link(config, message)
            return
        if not config.linked or message.user != config.owner_user_id or message.channel != config.dm_channel:
            return
        if self._bridge is None:
            await self._post(config.dm_channel, "No agent is chosen for this chat yet. Choose one in Settings, Integrations.")
            return
        await self._bridge.on_owner_message(text)

    async def _link(self, config: state.SlackConfig, message: DirectMessage) -> None:
        config.owner_user_id, config.dm_channel, config.thread_id = message.user, message.channel, None
        await self._save(config)
        await self._ensure_bridge(config)
        agent = None
        if config.agent_id is not None:
            async with self._rt.session() as db:
                agent = await agent_store.get_agent(db, config.agent_id)
        if agent is not None:
            reply = f"Linked. You can now talk to {agent.name} here. Send `new` to start a fresh conversation."
        else:
            reply = "Linked. Choose an agent in Settings, Integrations to start talking."
        await self._post(config.dm_channel, reply)

    async def _on_click(self, config: state.SlackConfig, click: ButtonClick) -> None:
        if not config.linked or click.user != config.owner_user_id or click.channel != config.dm_channel:
            return
        if click.team and click.team != config.team_id:
            return
        decision = {ACTION_ALLOW: ALLOW, ACTION_DENY: DENY}.get(click.action_id)
        if decision is None:
            return
        outcome = None
        if self._bridge is not None:
            outcome = await self._bridge.on_decision(click.value, decision)
        if outcome == RECORDED:
            return  # the card is replaced when the decision is read back, with who answered
        if isinstance(self._adapter, SlackAdapter):
            title = click.message_text.removeprefix("Approval needed: ").strip() or "Approval"
            await self._adapter.replace_card(
                click.channel, click.message_ts, title,
                "Already answered" if outcome == ALREADY_ANSWERED else "No longer waiting",
            )

    async def _post(self, channel: str | None, text: str) -> None:
        if self._api is None or not channel:
            return
        try:
            await self._api.post_message(channel, text)
        except Exception:  # noqa: BLE001
            logger.exception("a slack message could not be posted")


def _check_tokens(bot: str, app: str) -> None:
    if bot.startswith("xapp-") and app.startswith("xoxb-"):
        raise SlackSetupError("tokens_swapped", "The two tokens are the wrong way round: the bot token starts xoxb-, the app-level token starts xapp-.")
    if not bot.startswith("xoxb-"):
        raise SlackSetupError("invalid_bot_token", "The bot token must start with xoxb-.")
    if not app.startswith("xapp-"):
        raise SlackSetupError("invalid_app_token", "The app-level token must start with xapp-.")
    for token in (bot, app):
        if len(token) > MAX_TOKEN_CHARS or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise SlackSetupError("invalid_token", "A token must be one line of letters, numbers and dashes.")


_service: SlackService | None = None


def get_service() -> SlackService:
    """The one service of this process (created on first use)."""
    global _service
    if _service is None:
        from app.services.thread_runtime import runtime

        _service = SlackService(runtime)
    return _service
