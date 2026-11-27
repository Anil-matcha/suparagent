"""Discord DM parsing, token handling, and owner-only service rules."""

import json

import httpx
import pytest
from sqlalchemy import delete, select

from app.channels.base import ApprovalCard
from app.channels.discord.adapter import ACTION_ALLOW, DiscordAdapter
from app.channels.discord.api import DiscordApi, DiscordAuthError
from app.channels.discord.events import ButtonClick, DirectMessage, parse
from app.channels.discord.gateway import GatewayClient, gateway_url
from app.channels.discord.service import DiscordService, DiscordSetupError, LinkCodes
from app.core.config import settings
from app.db.models import Agent
from app.db.models.user_credential import UserCredential
from app.services import agent_store, thread_runtime
from app.services.provider_adapter import Capabilities
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.services.test_engine_fallback import Scripted
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401

TOKEN = "fake.discord.bot.token"
CAPS = Capabilities(tools=True, approvals=True, mcp=True, images=True, resume=True, computers=False)


def dm(text, user="123", channel="789", *, guild=False, bot=False, kind=0):
    data = {"id": "100", "channel_id": channel, "author": {"id": user, "bot": bot},
            "content": text, "type": kind}
    if guild:
        data["guild_id"] = "333"
    return {"op": 0, "t": "MESSAGE_CREATE", "s": 1, "d": data}


def click(user="123", channel="789", author="999", action=ACTION_ALLOW):
    return {"op": 0, "t": "INTERACTION_CREATE", "s": 2, "d": {
        "id": "101", "type": 3, "user": {"id": user}, "channel_id": channel,
        "data": {"component_type": 2, "custom_id": f"{action}:approval-1"},
        "message": {"id": "102", "content": "Approval needed: write", "author": {"id": author}},
    }}


def test_only_plain_dms_and_button_clicks_are_parsed():
    assert isinstance(parse(dm("hi")), DirectMessage)
    assert parse(dm("hi", guild=True)) is None
    assert parse(dm("edit", kind=19)) is None
    assert isinstance(parse(click()), ButtonClick)
    assert parse({"op": 0, "t": "MESSAGE_UPDATE", "d": dm("x")["d"]}) is None


def test_gateway_address_is_restricted_to_discord():
    assert gateway_url("wss://gateway.discord.gg") == "wss://gateway.discord.gg/?v=10&encoding=json"
    with pytest.raises(Exception):
        gateway_url("wss://gateway.discord.gg.evil.test/")


def test_link_code_expires_and_five_wrong_attempts_cancel_it():
    now = [0.0]
    codes = LinkCodes(clock=lambda: now[0])
    code, _ = codes.issue()
    for _ in range(5):
        assert not codes.redeem("999999" if code != "999999" else "888888")
    assert not codes.active
    code, _ = codes.issue()
    now[0] = 601.0
    assert not codes.redeem(code)


async def test_http_api_uses_bot_auth_and_disables_mentions():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "1", "channel_id": "789"})

    api = DiscordApi(TOKEN, transport=httpx.MockTransport(respond))
    await api.post_message("789", "@everyone")
    assert requests[0].headers["authorization"] == f"Bot {TOKEN}"
    assert json.loads(requests[0].content)["allowed_mentions"] == {"parse": []}
    assert str(requests[0].url) == "https://discord.com/api/v10/channels/789/messages"


async def test_interaction_ack_uses_token_only_in_discord_url():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(204)

    api = DiscordApi(TOKEN, transport=httpx.MockTransport(respond))
    await api.acknowledge("123", "interaction-token")
    assert "authorization" not in requests[0].headers
    assert requests[0].url.host == "discord.com"
    assert json.loads(requests[0].content) == {"type": 6}


async def test_approval_card_has_owner_buttons_only_when_full_request_fits():
    api = FakeApi(TOKEN)
    adapter = DiscordAdapter(api, "789")
    short = ApprovalCard("approval-1", "write_file", "notes.txt", "content", True)
    handle = await adapter.send_approval(short)
    assert handle == ("789", "1", "write_file: notes.txt")
    buttons = api.posts[-1][2][0]["components"]
    assert [button["custom_id"] for button in buttons] == [
        "approval_allow:approval-1", "approval_deny:approval-1",
    ]
    long = ApprovalCard("approval-2", "write_file", "notes.txt", "x" * 1800, True)
    await adapter.send_approval(long)
    assert api.posts[-1][2] == []
    assert "Open the app" in api.posts[-1][1]
    await adapter.close_approval(handle, "Allowed")
    assert api.edits[-1] == ("789", "1", "write_file: notes.txt\n*Allowed*", [])


class FakeSocket:
    def __init__(self, packets):
        self.packets = packets
        self.sent = []
        self.closed = False

    def __aiter__(self):
        self._iterator = iter(self.packets)
        return self

    async def __anext__(self):
        try:
            return json.dumps(next(self._iterator))
        except StopIteration:
            raise StopAsyncIteration from None

    async def send(self, value):
        self.sent.append(json.loads(value))

    async def close(self, code=1000):
        self.closed = True


async def test_gateway_identifies_with_dm_intent_and_resumes_from_accepted_sequence():
    class Api:
        def __init__(self):
            self.acks = []

        async def acknowledge(self, interaction_id, token):
            self.acks.append((interaction_id, token))

    api = Api()
    client = GatewayClient(api, TOKEN, lambda packet: None)
    ready = {"op": 0, "s": 1, "t": "READY", "d": {
        "session_id": "session", "resume_gateway_url": "wss://gateway.discord.gg",
    }}
    interaction = click()
    interaction["d"]["token"] = "secret"
    socket = FakeSocket([{"op": 10, "d": {"heartbeat_interval": 45000}}, ready, dm("hello"), interaction])
    await client._read(socket)
    assert socket.sent[0]["op"] == 2 and socket.sent[0]["d"]["intents"] == 1 << 12
    assert client._sequence == 2
    assert api.acks == [("101", "secret")]
    resumed = FakeSocket([{"op": 10, "d": {"heartbeat_interval": 45000}}])
    await client._read(resumed)
    assert resumed.sent[0] == {"op": 6, "d": {"token": TOKEN, "session_id": "session", "seq": 2}}


class FakeApi:
    def __init__(self, token):
        self.token = token
        self.posts = []
        self.edits = []
        self.who = {"id": "999", "username": "TestBot"}
        self.failure = None

    async def me(self):
        if self.failure:
            raise self.failure
        return self.who

    async def gateway(self):
        return "wss://gateway.discord.gg"

    async def channel(self, channel_id):
        return {"id": channel_id, "type": 1}

    async def post_message(self, channel, content, *, components=None):
        self.posts.append((channel, content, components))
        return {"id": str(len(self.posts)), "channel_id": channel}

    async def edit_message(self, channel, message_id, content, *, components=None):
        self.edits.append((channel, message_id, content, components))
        return {}


class FakeClient:
    def __init__(self, api, token, on_event, on_status=None):
        self.started = self.stopped = False

    def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


class FakeLock:
    async def ensure(self):
        return True

    async def release(self):
        pass


@pytest.fixture
async def world(sessions, owner, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    runtime = ThreadRuntime(hub=ThreadHub(), adapters={
        "claude": Scripted("claude", "Claude", None, caps=CAPS),
    }, status_ttl=0, _session_factory=sessions, owner_id=owner)
    apis = []

    def make_api(token):
        api = FakeApi(token)
        apis.append(api)
        return api

    service = DiscordService(runtime, api_factory=make_api, client_factory=FakeClient, lock=FakeLock())
    async with sessions() as db:
        agent = await agent_store.create_agent(db, owner_id=owner, name="Helper", provider="claude", workspace_path=str(tmp_path))
    yield service, runtime, apis, agent
    await service.stop()
    await runtime.shutdown()
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


async def test_connect_encrypts_token_and_never_returns_it(world, sessions, owner):
    service, _, apis, agent = world
    status = await service.connect(TOKEN)
    assert status["configured"] and status["bot"] == "TestBot" and status["agent_id"] == agent.id
    assert TOKEN not in json.dumps(status)
    async with sessions() as db:
        rows = (await db.execute(select(UserCredential).where(UserCredential.user_id == owner))).scalars().all()
    assert len(rows) == 1 and rows[0].provider == "integration:discord" and TOKEN not in rows[0].ciphertext
    assert len(apis) == 2


async def test_bad_token_is_rejected_before_a_request(world):
    service, _, apis, _ = world
    with pytest.raises(DiscordSetupError):
        await service.connect("bad\ntoken")
    assert apis == []


async def test_refused_token_is_not_saved(world, sessions, owner):
    service, _, _, _ = world
    original = service._api_factory

    def refuse(token):
        api = original(token)
        api.failure = DiscordAuthError("http_401")
        return api

    service._api_factory = refuse
    with pytest.raises(DiscordSetupError) as caught:
        await service.connect(TOKEN)
    assert caught.value.code == "discord_refused_token"
    async with sessions() as db:
        assert (await db.execute(select(UserCredential).where(UserCredential.user_id == owner))).scalars().all() == []


async def test_only_linked_owner_in_linked_dm_reaches_bridge(world):
    service, _, apis, _ = world
    await service.connect(TOKEN)
    code = (await service.issue_link_code())["code"]
    await service.handle_event(dm(f"link {code}", user="999", bot=True))
    assert not (await service.status())["linked"]
    await service.handle_event(dm(f"link {code}", guild=True))
    assert not (await service.status())["linked"]
    async def group_dm(channel_id):
        return {"id": channel_id, "type": 3}
    apis[-1].channel = group_dm
    await service.handle_event(dm(f"link {code}"))
    assert not (await service.status())["linked"]
    async def direct_message(channel_id):
        return {"id": channel_id, "type": 1}
    apis[-1].channel = direct_message
    await service.handle_event(dm(f"link {code}"))
    assert (await service.status())["linked"]
    assert apis[-1].posts[-1][1].startswith("Linked.")
    calls = []

    async def capture(text):
        calls.append(text)

    service._bridge.on_owner_message = capture
    for event in [dm("wrong user", user="456"), dm("wrong dm", channel="790"),
                  dm("bot", bot=True), dm("edit", kind=19), dm("guild", guild=True)]:
        await service.handle_event(event)
    assert calls == []
    await service.handle_event(dm("hello"))
    assert calls == ["hello"]


async def test_switching_to_a_different_bot_clears_the_link(world):
    service, _, _, _ = world
    await service.connect(TOKEN)
    code = (await service.issue_link_code())["code"]
    await service.handle_event(dm(f"link {code}"))
    assert (await service.status())["linked"]
    original = service._api_factory

    def new_bot(token):
        api = original(token)
        api.who = {"id": "888", "username": "OtherBot"}
        return api

    service._api_factory = new_bot
    status = await service.connect("another.bot.token")
    assert not status["linked"]
    assert not status["link_code_active"]


async def test_only_owner_can_press_a_button_on_this_bot_message(world):
    service, _, apis, _ = world
    await service.connect(TOKEN)
    code = (await service.issue_link_code())["code"]
    await service.handle_event(dm(f"link {code}"))
    calls = []

    async def decide(approval_id, decision):
        calls.append((approval_id, decision))
        return "recorded"

    service._bridge.on_decision = decide
    for event in [click(user="456"), click(channel="790"), click(author="888")]:
        await service.handle_event(event)
    assert calls == []
    await service.handle_event(click())
    assert calls == [("approval-1", "allow")]
    assert apis[-1].edits == []
