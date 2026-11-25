"""The Slack service: saving the connection, the link flow, and every rule that
decides whether a Slack event may do anything. A real runtime and database; a
fake Slack (no network, no WebSocket)."""

import json

import pytest
from sqlalchemy import delete, select

from app.channels.slack import state
from app.channels.slack.adapter import ACTION_ALLOW, ACTION_DENY, SlackAdapter
from app.channels.slack.api import SlackAuthError, SlackError
from app.channels.slack.service import LinkCodes, SlackService, SlackSetupError
from app.core.config import settings
from app.db.models import Agent
from app.db.models.user_credential import UserCredential
from app.services import agent_store, thread_runtime
from app.services.provider_adapter import Capabilities, TurnOutcome
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.services.test_engine_fallback import Scripted
from tests.services.thread_harness import cleanup, owner, sessions, wait_until  # noqa: F401

BOT = "xoxb" + "-1111111111-BOTBOTBOTBOT"
APP = "xapp" + "-1-A111-2222-APPAPPAPPAPPAPP"
FULL = Capabilities(tools=True, approvals=True, mcp=True, images=True, resume=True, computers=False)


class FakeApi:
    def __init__(self, bot, app):
        self.bot, self.app = bot, app
        self.posts: list[tuple[str, str, list | None]] = []
        self.updates: list[tuple[str, str, str, list | None]] = []
        self.team = {"ok": True, "team_id": "T1", "team": "Acme", "user_id": "UBOT", "bot_id": "B1"}
        self.auth_error: Exception | None = None

    async def auth_test(self):
        if self.auth_error:
            raise self.auth_error
        return self.team

    async def open_connection(self):
        return "wss://wss-primary.slack.com/link/"

    async def post_message(self, channel, text, *, blocks=None):
        self.posts.append((channel, text, blocks))
        return {"ok": True, "ts": f"{len(self.posts)}.0", "channel": channel}

    async def update_message(self, channel, ts, text, *, blocks=None):
        self.updates.append((channel, ts, text, blocks))
        return {"ok": True}


class FakeClient:
    def __init__(self, api, on_envelope, on_status=None):
        self.api, self.on_envelope, self.on_status = api, on_envelope, on_status
        self.started = self.stopped = False

    def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


class FakeLock:
    def __init__(self, held=True):
        self.held = held
        self.released = False

    async def ensure(self):
        return self.held

    async def release(self):
        self.released = True


def dm(text, user="U1", channel="D1", team="T1", **event):
    body = {"type": "message", "channel_type": "im", "user": user, "channel": channel, "text": text, "ts": "1.1"}
    body.update(event)
    return {"type": "events_api", "envelope_id": "e", "payload": {"event_id": "Ev", "team_id": team, "event": body}}


def click(action_id, value, user="U1", channel="D1", team="T1", text="Approval needed: Write: notes.txt"):
    return {"type": "interactive", "envelope_id": "e", "payload": {
        "type": "block_actions", "user": {"id": user}, "team": {"id": team}, "channel": {"id": channel},
        "message": {"ts": "9.9", "text": text}, "actions": [{"action_id": action_id, "value": value}]}}


class World:
    pass


@pytest.fixture
async def world(sessions, owner, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(thread_runtime, "APPROVAL_POLL_SECONDS", 0.1)
    engine = Scripted("claude", "Claude", None, caps=FULL)
    runtime = ThreadRuntime(hub=ThreadHub(), adapters={"claude": engine}, status_ttl=0, _session_factory=sessions, owner_id=owner)
    w = World()
    w.apis, w.clients, w.lock, w.engine, w.runtime, w.sessions, w.owner = [], [], FakeLock(), engine, runtime, sessions, owner

    def make_api(bot, app):
        api = FakeApi(bot, app)
        w.apis.append(api)
        return api

    def make_client(api, on_envelope, on_status=None):
        client = FakeClient(api, on_envelope, on_status)
        w.clients.append(client)
        return client

    w.service = SlackService(runtime, api_factory=make_api, client_factory=make_client, lock=w.lock, supervise_seconds=3600)
    async with sessions() as db:
        agent = await agent_store.create_agent(db, owner_id=owner, name="Helper", provider="claude", workspace_path=str(tmp_path))
    w.agent = agent

    async def reply(text):
        async def behaviour(ctx, sink, gate):
            await sink.emit("assistant.message", {"text": text})
            return TurnOutcome("succeeded", result=text, session_state="active")

        engine.behaviour = behaviour

    w.reply = reply

    async def linked():
        await w.service.connect(BOT, APP)
        code = (await w.service.issue_link_code())["code"]
        await w.service.handle_envelope(dm(f"link {code}"))

    w.linked = linked
    yield w
    await w.service.stop()
    await runtime.shutdown()
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


async def stored(world):
    async with world.sessions() as db:
        return (await db.execute(select(UserCredential).where(UserCredential.user_id == world.owner))).scalars().all()


# -- connecting -------------------------------------------------------------------------

async def test_connect_checks_both_tokens_with_slack_saves_them_encrypted_and_starts(world):
    status = await world.service.connect(BOT, APP)
    assert status["configured"] and status["team"] == "Acme" and status["linked"] is False
    assert status["agent_id"] == world.agent.id
    assert BOT not in json.dumps(status) and APP not in json.dumps(status)
    (row,) = await stored(world)
    assert row.provider == "integration:slack" and BOT not in row.ciphertext and APP not in row.ciphertext
    assert len(world.clients) == 1 and world.clients[0].started
    assert (world.apis[-1].bot, world.apis[-1].app) == (BOT, APP)


@pytest.mark.parametrize("bot,app,code", [
    ("not-a-token", APP, "invalid_bot_token"),
    (BOT, "xoxb" + "-wrong-kind-here", "invalid_app_token"),
    (APP, BOT, "tokens_swapped"),
    (BOT + " x", APP, "invalid_token"),
    ("xoxb-" + "a" * 600, APP, "invalid_token"),
])
async def test_a_malformed_token_is_refused_before_slack_is_asked_and_never_echoed(world, bot, app, code):
    with pytest.raises(SlackSetupError) as caught:
        await world.service.connect(bot, app)
    assert caught.value.code == code
    assert bot not in caught.value.message and app not in caught.value.message
    assert world.apis == [] and await stored(world) == []


async def test_tokens_slack_refuses_are_not_saved(world, monkeypatch):
    original = world.service._api_factory

    def refusing(bot, app):
        api = original(bot, app)
        api.auth_error = SlackAuthError("invalid_auth")
        return api

    world.service._api_factory = refusing
    with pytest.raises(SlackSetupError) as caught:
        await world.service.connect(BOT, APP)
    assert caught.value.code == "slack_refused_token" and "invalid_auth" in caught.value.message
    assert BOT not in caught.value.message and await stored(world) == []


async def test_slack_being_unreachable_is_a_502_and_saves_nothing(world):
    original = world.service._api_factory

    def down(bot, app):
        api = original(bot, app)
        api.auth_error = SlackError("network")
        return api

    world.service._api_factory = down
    with pytest.raises(SlackSetupError) as caught:
        await world.service.connect(BOT, APP)
    assert caught.value.status == 502 and await stored(world) == []


async def test_reconnecting_the_same_app_keeps_the_link_and_a_different_one_drops_it(world):
    await world.linked()
    again = await world.service.connect(BOT, APP + "2")
    assert again["linked"] is True
    other = world.service._api_factory

    def other_workspace(bot, app):
        api = other(bot, app)
        api.team = {"ok": True, "team_id": "T2", "team": "Other", "user_id": "UBOT2"}
        return api

    world.service._api_factory = other_workspace
    changed = await world.service.connect(BOT, APP + "3")
    assert changed["linked"] is False and changed["team"] == "Other"


async def test_disconnect_forgets_everything_and_stops(world):
    await world.linked()
    await world.service.disconnect()
    assert await stored(world) == []
    assert world.clients[-1].stopped
    assert (await world.service.status())["configured"] is False
    await world.service.handle_envelope(dm("hello"))
    assert world.engine.calls == []


async def test_a_hosted_deployment_gets_no_slack(world, monkeypatch):
    monkeypatch.setattr(settings, "TENANT_SCHEMAS_ENABLED", True)
    assert (await world.service.status())["available"] is False
    with pytest.raises(SlackSetupError) as caught:
        await world.service.connect(BOT, APP)
    assert caught.value.code == "not_available"
    await world.service.start()
    assert world.service._supervisor is None


async def test_a_backend_that_does_not_hold_the_lock_opens_no_connection(world):
    world.lock.held = False
    await world.service.connect(BOT, APP)
    assert world.clients == []
    assert (await world.service.status())["state"] == "standby"
    world.lock.held = True
    await world.service.apply()
    assert len(world.clients) == 1 and world.clients[0].started


async def test_losing_the_lock_closes_the_connection(world):
    await world.service.connect(BOT, APP)
    world.lock.held = False
    await world.service.apply()
    assert world.clients[0].stopped and (await world.service.status())["state"] == "standby"


async def test_the_status_reports_what_the_connection_says(world):
    await world.service.connect(BOT, APP)
    world.clients[0].on_status("failed", "token_revoked")
    status = await world.service.status()
    assert (status["state"], status["detail"]) == ("failed", "token_revoked")


# -- linking ----------------------------------------------------------------------------

async def test_the_code_links_the_sender_and_welcomes_them(world):
    await world.linked()
    api = world.apis[-1]
    assert api.posts[-1][0] == "D1" and "Linked" in api.posts[-1][1] and "Helper" in api.posts[-1][1]
    config = await world.service._load()
    assert config.owner_user_id == "U1" and config.dm_channel == "D1" and config.linked
    assert (await world.service.status())["linked"] is True


async def test_a_code_works_once(world):
    await world.service.connect(BOT, APP)
    code = (await world.service.issue_link_code())["code"]
    await world.service.handle_envelope(dm(f"link {code}", user="U1"))
    await world.service.handle_envelope(dm(f"link {code}", user="U2", channel="D2"))
    assert (await world.service._load()).owner_user_id == "U1"


async def test_a_wrong_code_is_ignored_in_silence_and_five_cancel_the_real_one(world):
    await world.service.connect(BOT, APP)
    code = (await world.service.issue_link_code())["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        await world.service.handle_envelope(dm(f"link {wrong}", user="U9", channel="D9"))
    assert world.apis[-1].posts == []
    await world.service.handle_envelope(dm(f"link {code}"))
    config = await world.service._load()
    assert not config.linked and world.apis[-1].posts == []


async def test_without_a_code_link_does_nothing_and_says_nothing(world):
    await world.service.connect(BOT, APP)
    await world.service.handle_envelope(dm("link 123456"))
    assert not (await world.service._load()).linked and world.apis[-1].posts == []


async def test_the_app_itself_can_never_become_the_owner(world):
    await world.service.connect(BOT, APP)
    code = (await world.service.issue_link_code())["code"]
    await world.service.handle_envelope(dm(f"link {code}", user="UBOT", channel="DBOT"))
    assert not (await world.service._load()).linked


def test_a_code_expires():
    now = [0.0]
    codes = LinkCodes(clock=lambda: now[0])
    code, ttl = codes.issue()
    assert len(code) == 6 and code.isdigit() and ttl == 600 and codes.active
    now[0] = 601
    assert not codes.active and codes.redeem(code) is False


async def test_a_new_code_links_another_slack_account_and_restarts_the_conversation(world):
    await world.linked()
    await world.reply("hi")
    await world.service.handle_envelope(dm("first"))
    await wait_until(lambda: world.apis[-1].posts and "hi" in [p[1] for p in world.apis[-1].posts])
    assert (await world.service._load()).thread_id is not None
    code = (await world.service.issue_link_code())["code"]
    await world.service.handle_envelope(dm(f"LINK {code}", user="U2", channel="D2"))
    config = await world.service._load()
    assert (config.owner_user_id, config.dm_channel, config.thread_id) == ("U2", "D2", None)


async def test_the_code_needs_a_connection_first(world):
    with pytest.raises(SlackSetupError) as caught:
        await world.service.issue_link_code()
    assert caught.value.code == "not_connected" and caught.value.status == 409


# -- messages from the owner and everyone else ------------------------------------------

async def nothing_was_started(world, label=""):
    """A message that got through starts a conversation and a relay straight
    away (the turn itself runs in the background, so the engine is not checked)."""
    assert (await world.service._load()).thread_id is None, label
    bridge = world.service._bridge
    assert bridge is None or not bridge._relays, label
    assert world.engine.calls == [], label


async def test_the_owners_message_reaches_the_agent_and_the_answer_comes_back_to_their_dm(world):
    await world.linked()
    await world.reply("Hello from **the agent**")
    await world.service.handle_envelope(dm("hi there"))
    await wait_until(lambda: any("Hello from" in p[1] for p in world.apis[-1].posts))
    channel, text, _ = world.apis[-1].posts[-1]
    assert channel == "D1" and text == "Hello from *the agent*"
    assert len(world.engine.calls) == 1


@pytest.mark.parametrize("label,message", [
    ("another user", dm("hi", user="U2")),
    ("another workspace", dm("hi", team="T9")),
    ("another dm channel", dm("hi", channel="D2")),
    ("a bot", dm("hi", bot_id="B7")),
    ("the app itself", dm("hi", user="UBOT")),
    ("an edit", dm("hi", subtype="message_changed")),
    ("a channel message", dm("hi", channel_type="channel")),
    ("a mention", dm("hi", type="app_mention")),
    ("an empty text", dm("   ")),
])
async def test_everything_but_the_owners_plain_message_is_ignored(world, label, message):
    await world.linked()
    posts_before = len(world.apis[-1].posts)
    await world.service.handle_envelope(message)
    await nothing_was_started(world, label)
    assert len(world.apis[-1].posts) == posts_before, label


async def test_before_linking_every_message_is_ignored(world):
    await world.service.connect(BOT, APP)
    await world.service.handle_envelope(dm("do something"))
    await nothing_was_started(world)
    assert world.apis[-1].posts == []


async def test_no_agent_chosen_is_explained(world):
    async with world.sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == world.owner))
        await db.commit()
    await world.linked()
    await world.service.handle_envelope(dm("hi"))
    assert "No agent is chosen" in world.apis[-1].posts[-1][1]


async def test_choosing_an_agent_validates_it_and_starts_a_fresh_conversation(world):
    await world.linked()
    with pytest.raises(SlackSetupError) as caught:
        await world.service.set_agent(99_999_999)
    assert caught.value.code == "agent_unavailable"
    async with world.sessions() as db:
        other = await agent_store.create_agent(db, owner_id=world.owner, name="Second", provider="claude", workspace_path=world.agent.workspace_path)
    await world.service._save_thread_id(123)
    status = await world.service.set_agent(other.id)
    assert status["agent_id"] == other.id
    assert (await world.service._load()).thread_id is None


async def test_an_agent_of_another_owner_or_archived_is_refused(world):
    await world.linked()
    async with world.sessions() as db:
        foreign = await agent_store.create_agent(db, owner_id=world.owner + "x", name="Foreign", provider="claude", workspace_path=world.agent.workspace_path)
    try:
        with pytest.raises(SlackSetupError):
            await world.service.set_agent(foreign.id)
    finally:
        async with world.sessions() as db:
            await db.execute(delete(Agent).where(Agent.id == foreign.id))
            await db.commit()


# -- approvals --------------------------------------------------------------------------

async def asks(world):
    from uuid import uuid4

    async def behaviour(ctx, sink, gate):
        handle = await gate.open(request_id=uuid4().hex, tool="Write", input={"file_path": "notes.txt"}, summary="Write notes.txt", kind=None)
        decision = await gate.decide(handle)
        await sink.emit("assistant.message", {"text": f"decision: {decision.kind}"})
        return TurnOutcome("succeeded", result="done", session_state="active")

    world.engine.behaviour = behaviour


def cards(world):
    return [p for p in world.apis[-1].posts if p[2]]


async def test_a_card_with_buttons_is_posted_and_the_owners_allow_continues_the_turn(world):
    await world.linked()
    await asks(world)
    await world.service.handle_envelope(dm("write it"))
    await wait_until(lambda: cards(world))
    channel, fallback, blocks = cards(world)[0]
    assert channel == "D1" and fallback.startswith("Approval needed: Write")
    buttons = blocks[1]["elements"]
    assert [b["action_id"] for b in buttons] == [ACTION_ALLOW, ACTION_DENY]
    approval_id = buttons[0]["value"]
    await world.service.handle_envelope(click(ACTION_ALLOW, approval_id))
    await wait_until(lambda: any("decision: allow" in p[1] for p in world.apis[-1].posts))
    await wait_until(lambda: world.apis[-1].updates)
    assert world.apis[-1].updates[0][2].endswith("Allowed")


async def test_deny_from_the_owner_denies(world):
    await world.linked()
    await asks(world)
    await world.service.handle_envelope(dm("write it"))
    await wait_until(lambda: cards(world))
    approval_id = cards(world)[0][2][1]["elements"][1]["value"]
    await world.service.handle_envelope(click(ACTION_DENY, approval_id))
    await wait_until(lambda: any("decision: deny" in p[1] for p in world.apis[-1].posts))


@pytest.mark.parametrize("label,changes", [
    ("another user", {"user": "U2"}),
    ("another workspace", {"team": "T9"}),
    ("another channel", {"channel": "D2"}),
    ("an unknown action", {"action_id": "something_else"}),
])
async def test_a_click_from_anyone_or_anything_else_changes_nothing(world, label, changes):
    await world.linked()
    await asks(world)
    await world.service.handle_envelope(dm("write it"))
    await wait_until(lambda: cards(world))
    approval_id = cards(world)[0][2][1]["elements"][0]["value"]
    args = {"action_id": ACTION_ALLOW, "value": approval_id}
    args.update(changes)
    await world.service.handle_envelope(click(args.pop("action_id"), args.pop("value"), **args))
    async with world.sessions() as db:
        from app.services import thread_store as store

        thread_id = (await world.service._load()).thread_id
        pending = await store.list_approvals(db, thread_id, status="pending")
    assert len(pending) == 1, label
    assert world.apis[-1].updates == [], label
    # Clean up: answer in the app so the turn ends.
    await world.runtime.answer_approval(approval_id, "deny")


async def test_a_press_on_a_card_nobody_is_waiting_for_marks_it(world):
    await world.linked()
    await world.service.handle_envelope(click(ACTION_ALLOW, "gone-123"))
    channel, ts, text, blocks = world.apis[-1].updates[0]
    assert (channel, ts) == ("D1", "9.9") and text == "Write: notes.txt: No longer waiting"


# -- the Slack adapter ------------------------------------------------------------------

async def test_the_adapter_posts_to_its_channel_and_shows_a_big_request_without_buttons():
    from app.channels.base import ApprovalCard

    api = FakeApi(BOT, APP)
    adapter = SlackAdapter(api, "D1")
    await adapter.send_text("hello")
    assert api.posts[0][:2] == ("D1", "hello")
    handle = await adapter.send_approval(ApprovalCard("ap-1", "Write", "Write a <big> file", "a ``` b & c", interactive=False))
    _, _, blocks = api.posts[-1]
    text = blocks[0]["text"]["text"]
    assert "&lt;big&gt;" in text and "a ''' b &amp; c" in text and text.count("```") == 2
    assert blocks[1]["type"] == "context" and "Open the app" in blocks[1]["elements"][0]["text"]
    assert handle == ("D1", "2.0", "Write: Write a <big> file")
    await adapter.close_approval(handle, "Denied")
    assert api.updates[-1][:3] == ("D1", "2.0", "Write: Write a <big> file: Denied")
    await adapter.close_approval(None, "ignored")
    assert len(api.updates) == 1
