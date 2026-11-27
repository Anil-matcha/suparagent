"""Branching a conversation from a past message (app/services/thread_branch.py)."""

import pytest

from app.services import thread_store as store
from app.services.thread_branch import BranchError, branch_thread
from tests.services.test_engine_fallback import (  # noqa: F401
    Scripted,
    agent_cleanup,
    conversation,
    make_rt,
    quiet,
    replies,
)
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401


async def _chat(make_rt, sessions, owner, tmp_path):
    primary = Scripted("primary", "Primary", replies, sessions=True)
    rt = make_rt(primary=primary)
    thread = await conversation(rt, sessions, owner, tmp_path, fallback=None)
    await rt.send_turn(thread.id, "Remember the project is called Orchard")
    await finished(sessions, thread.id, 1)
    await rt.send_turn(thread.id, "Pick a colour")
    await finished(sessions, thread.id, 2)
    async with sessions() as db:
        turns = await store.list_turns(db, thread.id)
    return rt, primary, thread, turns


async def test_branch_copies_earlier_turns_and_leaves_the_original(make_rt, sessions, owner, tmp_path):
    rt, primary, thread, turns = await _chat(make_rt, sessions, owner, tmp_path)
    before = len(await events_of(sessions, thread.id))
    branch = await branch_thread(rt, thread.id, turns[1].id)
    assert branch.id != thread.id
    assert branch.branched_from_thread_id == thread.id
    assert branch.provider_session_id != thread.provider_session_id
    async with sessions() as db:
        copied = await store.list_turns(db, branch.id)
    assert [t.prompt_text for t in copied] == ["Remember the project is called Orchard"]
    assert copied[0].owner_token is None and copied[0].id != turns[0].id
    prompts = [e.data["prompt_text"] for e in await events_of(sessions, branch.id) if e.type == "turn.started"]
    assert prompts == ["Remember the project is called Orchard"]
    assert len(await events_of(sessions, thread.id)) == before


async def test_branch_gives_the_new_session_the_earlier_history_once(make_rt, sessions, owner, tmp_path):
    rt, primary, thread, turns = await _chat(make_rt, sessions, owner, tmp_path)
    branch = await branch_thread(rt, thread.id, turns[1].id)
    await rt.send_turn(branch.id, "Pick a different colour")
    await finished(sessions, branch.id, 2)
    prompt = primary.calls[-1].prompt
    assert "project is called Orchard" in prompt
    assert prompt.endswith("Pick a different colour")
    await rt.send_turn(branch.id, "And a number")
    await finished(sessions, branch.id, 3)
    assert primary.calls[-1].prompt == "And a number"


async def test_branch_from_the_first_message_starts_empty(make_rt, sessions, owner, tmp_path):
    rt, primary, thread, turns = await _chat(make_rt, sessions, owner, tmp_path)
    branch = await branch_thread(rt, thread.id, turns[0].id)
    assert await events_of(sessions, branch.id) == []
    await rt.send_turn(branch.id, "Different opening")
    await finished(sessions, branch.id, 1)
    assert primary.calls[-1].prompt == "Different opening"


async def test_branch_refuses_other_owners_and_unknown_turns(make_rt, sessions, owner, tmp_path):
    rt, primary, thread, turns = await _chat(make_rt, sessions, owner, tmp_path)
    stranger = make_rt(primary=primary)
    stranger.owner_id = "someone_else"
    with pytest.raises(store.ThreadNotFound):
        await branch_thread(stranger, thread.id, turns[1].id)
    with pytest.raises(BranchError) as error:
        await branch_thread(rt, thread.id, 10**9)
    assert error.value.code == "turn_not_found"
