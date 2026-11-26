"""The channel bridge against a real runtime with a scripted engine and a fake
chat app. Nothing here talks to Slack or any network."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.channels.base import ApprovalCard
from app.channels.bridge import (
    ALREADY_ANSWERED,
    BUSY,
    MAX_INBOUND_CHARS,
    NOTHING_TO_SAY,
    RECORDED,
    UNKNOWN,
    ChannelBridge,
)
from app.core.config import settings
from app.db.models import Agent
from app.services import thread_runtime
from app.services.provider_adapter import ALLOW, DENY, Capabilities, TurnOutcome
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.services.test_engine_fallback import Scripted
from tests.services.thread_harness import cleanup, owner, sessions, wait_until  # noqa: F401

FULL = Capabilities(tools=True, approvals=True, mcp=True, images=True, resume=True, computers=False)


class FakeChat:
    """A chat app that records what it was asked to post."""

    name = "Slack"
    max_chars = 400
    card_chars = 600

    def __init__(self):
        self.sent: list[str] = []
        self.cards: list[ApprovalCard] = []
        self.closed: list[tuple[object, str]] = []

    def render(self, text):
        return text.replace("**", "*")

    async def send_text(self, text):
        self.sent.append(text)

    async def send_approval(self, card):
        self.cards.append(card)
        return f"card-{len(self.cards)}"

    async def close_approval(self, handle, outcome):
        self.closed.append((handle, outcome))


class State:
    """Where the bridge keeps its conversation id (the real one is the Slack credential)."""

    def __init__(self, thread_id=None):
        self.thread_id = thread_id

    async def load(self):
        return self.thread_id

    async def save(self, thread_id):
        self.thread_id = thread_id


async def say(text):
    async def behaviour(ctx, sink, gate):
        await sink.emit("assistant.message", {"text": text})
        return TurnOutcome("succeeded", result=text, session_state="active")

    return behaviour


@pytest.fixture
async def world(sessions, owner, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(thread_runtime, "APPROVAL_POLL_SECONDS", 0.1)
    engine = Scripted("claude", "Claude", None, caps=FULL)
    runtime = ThreadRuntime(
        hub=ThreadHub(), adapters={"claude": engine}, status_ttl=0, _session_factory=sessions, owner_id=owner
    )
    chat, state = FakeChat(), State()
    bridge = ChannelBridge(
        runtime, chat, agent_id=None, load_thread_id=state.load, save_thread_id=state.save,
        still_working_seconds=0.3, poll_seconds=0.05,
    )

    async def behave(function):
        async def run(ctx, sink, gate):
            return await function(ctx, sink, gate)

        engine.behaviour = run

    class World:
        pass

    w = World()
    w.runtime, w.chat, w.state, w.bridge, w.engine, w.behave, w.sessions = runtime, chat, state, bridge, engine, behave, sessions
    yield w
    await bridge.close()
    await runtime.shutdown()
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


async def replied(world, count=1):
    await wait_until(lambda: len(world.chat.sent) >= count)


async def test_an_answer_is_posted_in_the_chat_apps_markup_and_the_conversation_is_kept(world):
    await world.behave(await say("Here is **bold** news."))
    await world.bridge.on_owner_message("hello")
    await replied(world)
    assert world.chat.sent == ["Here is *bold* news."]
    first = world.state.thread_id
    assert first is not None
    await world.bridge.on_owner_message("again")
    await replied(world, 2)
    assert world.state.thread_id == first


async def test_new_starts_another_conversation(world):
    await world.behave(await say("ok"))
    await world.bridge.on_owner_message("hello")
    await replied(world)
    first = world.state.thread_id
    await world.bridge.on_owner_message("NEW")
    assert world.chat.sent[-1] == "Started a new conversation."
    assert world.state.thread_id is None
    await world.bridge.on_owner_message("hello again")
    await replied(world, 3)
    assert world.state.thread_id not in (None, first)


async def test_a_message_that_is_too_long_starts_nothing(world):
    await world.behave(await say("should not run"))
    await world.bridge.on_owner_message("x" * (MAX_INBOUND_CHARS + 1))
    assert "too long" in world.chat.sent[-1] and "8,000" in world.chat.sent[-1]
    assert world.state.thread_id is None
    assert world.engine.calls == []


async def test_blank_messages_are_ignored(world):
    await world.bridge.on_owner_message("   ")
    assert world.chat.sent == [] and world.engine.calls == []


async def test_a_second_message_while_busy_is_told_so_and_the_first_still_answers(world):
    release = asyncio.Event()

    async def slow(ctx, sink, gate):
        await release.wait()
        await sink.emit("assistant.message", {"text": "finally"})
        return TurnOutcome("succeeded", result="finally", session_state="active")

    await world.behave(slow)
    await world.bridge.on_owner_message("first")
    await wait_until(lambda: world.engine.calls)
    await world.bridge.on_owner_message("second")
    assert BUSY in world.chat.sent
    await wait_until(lambda: any("Still working on it" in s for s in world.chat.sent))
    release.set()
    await wait_until(lambda: "finally" in world.chat.sent)
    assert len(world.engine.calls) == 1


async def test_new_is_refused_while_a_reply_is_still_coming(world):
    release = asyncio.Event()

    async def slow(ctx, sink, gate):
        await release.wait()
        return TurnOutcome("succeeded", result="x", session_state="active")

    await world.behave(slow)
    await world.bridge.on_owner_message("first")
    await wait_until(lambda: world.engine.calls)
    kept = world.state.thread_id
    await world.bridge.on_owner_message("new")
    assert "Wait for the current reply" in world.chat.sent[-1]
    assert world.state.thread_id == kept
    release.set()
    await wait_until(lambda: NOTHING_TO_SAY in world.chat.sent)


async def test_a_failed_turn_says_why_in_one_line(world):
    async def fails(ctx, sink, gate):
        return TurnOutcome("failed", error="the engine\nsaid  boom", error_code="cli_error")

    await world.behave(fails)
    await world.bridge.on_owner_message("go")
    await wait_until(lambda: world.chat.sent)
    assert world.chat.sent == ["That did not finish: the engine said boom"]


async def test_an_engine_fallback_is_announced_in_the_chat(world):
    event = SimpleNamespace(type="engine.switched", data={
        "message": "Switched this turn to Codex because Claude reached its usage limit."
    })
    assert await world.bridge._handle(event, []) is False
    assert world.chat.sent == ["Switched this turn to Codex because Claude reached its usage limit."]


async def test_partial_text_of_a_failed_turn_is_posted_first(world):
    async def half(ctx, sink, gate):
        await sink.emit("assistant.message", {"text": "Starting on it."})
        return TurnOutcome("failed", error="limit", error_code="cli_error")

    await world.behave(half)
    await world.bridge.on_owner_message("go")
    await replied(world, 2)
    assert world.chat.sent == ["Starting on it.", "That did not finish: limit"]


async def test_a_long_answer_is_cut_into_pieces_that_fit(world):
    text = "\n\n".join(f"Paragraph {i}: " + "word " * 30 for i in range(8))
    await world.behave(await say(text))
    await world.bridge.on_owner_message("long")
    await wait_until(lambda: len(world.chat.sent) >= 3)
    assert all(len(piece) <= world.chat.max_chars for piece in world.chat.sent)


async def test_credential_looking_text_never_reaches_the_chat(world):
    secret = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123"
    await world.behave(await say(f"The key is {secret} and api_key=hunter2hunter2"))
    await world.bridge.on_owner_message("show me")
    await replied(world)
    posted = " ".join(world.chat.sent)
    assert secret not in posted and "hunter2hunter2" not in posted
    assert "[redacted]" in posted


async def test_a_deleted_conversation_is_replaced(world):
    await world.behave(await say("hi"))
    world.state.thread_id = 987_654_321
    await world.bridge.on_owner_message("hello")
    await replied(world)
    assert world.state.thread_id not in (None, 987_654_321)


async def test_an_unavailable_agent_is_explained(world):
    world.bridge.agent_id = 99_999_999
    await world.bridge.on_owner_message("hello")
    assert "not available" in world.chat.sent[-1]
    assert world.engine.calls == []


async def asks(world, tool="Write", tool_input=None, summary="Write notes.txt"):
    decided = []

    async def behaviour(ctx, sink, gate):
        handle = await gate.open(request_id=uuid4().hex, tool=tool, input=tool_input or {"file_path": "notes.txt"}, summary=summary, kind=None)
        decision = await gate.decide(handle)
        decided.append(decision.kind)
        await sink.emit("assistant.message", {"text": f"decision: {decision.kind}"})
        return TurnOutcome("succeeded", result="done", session_state="active")

    await world.behave(behaviour)
    return decided


async def test_allow_from_the_chat_lets_the_turn_continue_and_closes_the_card(world):
    decided = await asks(world)
    await world.bridge.on_owner_message("write it")
    await wait_until(lambda: world.chat.cards)
    card = world.chat.cards[0]
    assert card.interactive and card.tool == "Write" and "notes.txt" in card.detail
    assert await world.bridge.on_decision(card.approval_id, ALLOW) == RECORDED
    await wait_until(lambda: "decision: allow" in world.chat.sent)
    assert decided == [ALLOW]
    assert world.chat.closed == [("card-1", "Allowed")]


async def test_deny_from_the_chat_is_recorded_as_denied(world):
    decided = await asks(world)
    await world.bridge.on_owner_message("write it")
    await wait_until(lambda: world.chat.cards)
    assert await world.bridge.on_decision(world.chat.cards[0].approval_id, DENY) == RECORDED
    await wait_until(lambda: "decision: deny" in world.chat.sent)
    assert decided == [DENY]
    assert world.chat.closed == [("card-1", "Denied")]


async def test_the_second_press_and_an_unknown_card_change_nothing(world):
    await asks(world)
    await world.bridge.on_owner_message("write it")
    await wait_until(lambda: world.chat.cards)
    approval_id = world.chat.cards[0].approval_id
    assert await world.bridge.on_decision(approval_id, ALLOW) == RECORDED
    await wait_until(lambda: world.chat.closed)
    assert await world.bridge.on_decision(approval_id, DENY) == UNKNOWN
    assert await world.bridge.on_decision("not-an-approval", ALLOW) == UNKNOWN


async def test_an_answer_given_in_the_app_updates_the_card(world):
    await asks(world)
    await world.bridge.on_owner_message("write it")
    await wait_until(lambda: world.chat.cards)
    assert await world.runtime.answer_approval(world.chat.cards[0].approval_id, ALLOW) is True
    await wait_until(lambda: world.chat.closed)
    assert world.chat.closed == [("card-1", "Allowed in the app")]
    # The chat app's own late press finds the card already answered.
    assert await world.bridge.on_decision(world.chat.cards[0].approval_id, DENY) == UNKNOWN


async def test_a_request_too_big_for_one_card_has_no_buttons(world):
    await asks(world, tool_input={"content": "line\n" * 400}, summary="Write a big file")
    await world.bridge.on_owner_message("write it")
    await wait_until(lambda: world.chat.cards)
    card = world.chat.cards[0]
    assert not card.interactive
    assert len(card.tool) + len(card.summary) + len(card.detail) <= world.chat.card_chars
    await world.runtime.answer_approval(card.approval_id, DENY)
    await wait_until(lambda: world.chat.closed)


async def test_a_card_never_shows_credential_looking_input(world):
    await asks(world, tool_input={"command": "curl -H 'x-api-key: abcdef1234567890' https://x.test", "env": "sk-proj-ABCDEFGHIJKLMNOP1234"})
    await world.bridge.on_owner_message("run it")
    await wait_until(lambda: world.chat.cards)
    shown = world.chat.cards[0].detail + world.chat.cards[0].summary
    assert "abcdef1234567890" not in shown and "sk-proj-ABCDEFGHIJKLMNOP1234" not in shown
    await world.runtime.answer_approval(world.chat.cards[0].approval_id, DENY)
    await wait_until(lambda: world.chat.closed)


async def test_the_chat_app_is_recorded_as_who_answered(world, sessions):
    from app.services import thread_store as store

    await asks(world)
    await world.bridge.on_owner_message("write it")
    await wait_until(lambda: world.chat.cards)
    approval_id = world.chat.cards[0].approval_id
    await world.bridge.on_decision(approval_id, ALLOW)
    await wait_until(lambda: world.chat.closed)
    async with sessions() as db:
        events = await store.list_events(db, world.state.thread_id)
    resolved = [e.data for e in events if e.type == "approval.resolved"]
    assert len(resolved) == 1
    assert resolved[0]["status"] == "allowed"
    assert resolved[0]["person"]["label"] == "Slack"
