"""The owner's feedback on shadow-mode predictions (BL-175): a fixed reason list, one row per target, scoped by owner, agent and
share, and never any mail text."""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import gmail_share
from app.db.models import Agent, ShadowFeedback
from app.services import agent_store, shadow_feedback as fb
from tests.services.test_composio_bridge import world  # noqa: F401
from tests.services.test_composio_gmail_share import ACCOUNT, entry, rule
from tests.services.test_shadow_report import NOW, fake, predict, ready  # noqa: F401
from tests.services.test_share_suggestion import threads_in
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401


async def give(sessions, owner, agent, thread, **kw):
    kw.setdefault("kind", "draft")
    kw.setdefault("reason", "wrong_tone")
    async with sessions() as db:
        return await fb.record(db, owner, agent.id, thread_id=thread, **kw)


async def rows(sessions):
    async with sessions() as db:
        return list((await db.execute(select(ShadowFeedback).order_by(ShadowFeedback.id))).scalars())


@pytest.fixture(autouse=True)
async def clean(sessions, owner):
    yield
    async with sessions() as db:
        for row in (await db.execute(select(ShadowFeedback))).scalars():
            await db.delete(row)
        await db.commit()


async def predicted(world, fake, sessions, owner, shares=None):
    runtime, agent = await ready(world, fake, sessions, shares if shares is not None else {"gmail": {ACCOUNT: entry(50)}})
    thread = threads_in(50, 1)[0]
    await predict(sessions, owner, agent, thread, 1, NOW - timedelta(days=2), draft="a draft", turn=1)
    return agent, thread


async def test_feedback_is_recorded_and_changing_your_mind_replaces_the_row(world, fake, sessions, owner):
    agent, thread = await predicted(world, fake, sessions, owner)
    first = await give(sessions, owner, agent, thread, reason="wrong_tone")
    assert first == {"thread_id": thread, "kind": "draft", "reason": "wrong_tone", "recorded": True}
    await give(sessions, owner, agent, thread, reason="missing_context", note="needed the order history")
    (row,) = await rows(sessions)
    assert (row.reason, row.note, row.kind, row.account_id, row.agent_id) == ("missing_context", "needed the order history", "draft", ACCOUNT, agent.id)


async def test_a_draft_and_a_ranking_are_separate_targets(world, fake, sessions, owner):
    agent, thread = await predicted(world, fake, sessions, owner)
    await give(sessions, owner, agent, thread, kind="draft", reason="wrong_tone")
    await give(sessions, owner, agent, thread, kind="ranking", reason="wrong_priority")
    assert sorted((r.kind, r.reason) for r in await rows(sessions)) == [("draft", "wrong_tone"), ("ranking", "wrong_priority")]


@pytest.mark.parametrize("field,value,code", [
    ("reason", "boring", "invalid_reason"), ("reason", None, "invalid_reason"), ("kind", "label", "invalid_kind"),
    ("note", "x" * 301, "invalid_note"), ("note", 5, "invalid_note"), ("thread_id", "", "invalid_thread"), ("thread_id", "t" * 201, "invalid_thread"),
    ("account_id", "", "invalid_account"),
])
async def test_bad_values_are_refused_and_nothing_is_stored(world, fake, sessions, owner, field, value, code):
    agent, thread = await predicted(world, fake, sessions, owner)
    kw = {"thread": thread}
    async with sessions() as db:
        args = {"thread_id": thread, "kind": "draft", "reason": "wrong_tone", field: value}
        with pytest.raises(fb.FeedbackError) as caught:
            await fb.record(db, owner, agent.id, **args)
    assert caught.value.code == code and caught.value.status == 422 and kw and await rows(sessions) == []


async def test_the_note_is_capped_and_stripped_of_secrets_and_personal_details(world, fake, sessions, owner):
    agent, thread = await predicted(world, fake, sessions, owner)
    note = "call me at 415 555 0100 or bob@example.com,   api_key=abcdef1234567890abcdef\n\nsee https://x.example/a?token=zzz"
    await give(sessions, owner, agent, thread, note=note)
    (row,) = await rows(sessions)
    assert "bob@example.com" not in row.note and "abcdef1234567890abcdef" not in row.note and "token=zzz" not in row.note
    assert "\n" not in row.note and len(row.note) <= fb.NOTE_MAX
    await give(sessions, owner, agent, thread, note="   ")
    assert (await rows(sessions))[0].note is None  # an empty note is no note


async def test_a_thread_without_a_prediction_is_refused(world, fake, sessions, owner):
    agent, _ = await predicted(world, fake, sessions, owner)
    with pytest.raises(fb.FeedbackError) as caught:
        await give(sessions, owner, agent, "th999999")
    assert caught.value.code == "no_prediction" and caught.value.status == 404


async def test_a_thread_outside_the_current_share_is_refused(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions, {"gmail": {ACCOUNT: entry(5)}})
    outside = next(f"th{n}" for n in range(100_000) if not gmail_share.in_share(rule(5), f"th{n}"))
    await predict(sessions, owner, agent, outside, 1, NOW - timedelta(days=2), draft="d", turn=1)  # predicted while the share was wider
    with pytest.raises(fb.FeedbackError) as caught:
        await give(sessions, owner, agent, outside)
    assert caught.value.code == "outside_share" and caught.value.status == 403 and await rows(sessions) == []


async def test_lowering_the_share_stops_feedback_on_threads_that_left_it(world, fake, sessions, owner):
    agent, thread = await predicted(world, fake, sessions, owner, {"gmail": {ACCOUNT: entry(50)}})
    await give(sessions, owner, agent, thread)
    narrow = next(f"th{n}" for n in range(100_000) if gmail_share.in_share(rule(50), f"th{n}") and not gmail_share.in_share(rule(5), f"th{n}"))
    await predict(sessions, owner, agent, narrow, 2, NOW - timedelta(days=2), draft="d", turn=2)
    async with sessions() as db:
        (await db.get(Agent, agent.id)).connector_shares = {"gmail": {ACCOUNT: entry(5)}}
        await db.commit()
    with pytest.raises(fb.FeedbackError) as caught:
        await give(sessions, owner, agent, narrow)
    assert caught.value.code == "outside_share"


async def test_a_damaged_share_and_a_removed_account_are_refused(world, fake, sessions, owner):
    agent, thread = await predicted(world, fake, sessions, owner)
    async with sessions() as db:
        (await db.get(Agent, agent.id)).connector_shares = {"gmail": {ACCOUNT: {"percent": 0}}}
        await db.commit()
    with pytest.raises(fb.FeedbackError) as caught:
        await give(sessions, owner, agent, thread)
    assert (caught.value.code, caught.value.status) == ("share_invalid", 409)
    async with sessions() as db:
        await agent_store.update_agent(db, agent.id, connector_scopes={}, connector_account_scopes={})
    with pytest.raises(fb.FeedbackError) as caught:
        await give(sessions, owner, agent, thread)
    assert (caught.value.code, caught.value.status) == ("account_not_allowed", 409)


async def test_another_owners_agent_is_not_reachable(world, fake, sessions, owner):
    agent, thread = await predicted(world, fake, sessions, owner)
    async with sessions() as db:
        theirs = await agent_store.create_agent(db, owner_id="someone-else-" + uuid4().hex, name="X")
    async with sessions() as db:
        with pytest.raises(fb.FeedbackError) as caught:
            await fb.record(db, owner, theirs.id, thread_id=thread, kind="draft", reason="other")
        assert caught.value.code == "agent_not_found"
        assert await fb.counts(db, owner, theirs.id, since=NOW - timedelta(days=28)) is None
        assert await fb.for_lessons(db, owner, theirs.id, since=NOW - timedelta(days=28)) == []


async def test_counts_by_reason_and_kind_within_the_window(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions, {"gmail": {ACCOUNT: entry(50)}})
    threads = threads_in(50, 4)
    for i, thread in enumerate(threads):
        await predict(sessions, owner, agent, thread, i + 1, NOW - timedelta(days=2), draft="d", turn=i)
    await give(sessions, owner, agent, threads[0], reason="wrong_tone")
    await give(sessions, owner, agent, threads[1], reason="wrong_tone", kind="ranking")
    await give(sessions, owner, agent, threads[2], reason="wrong_policy")
    await give(sessions, owner, agent, threads[3], reason="other")
    async with sessions() as db:
        old = (await db.execute(select(ShadowFeedback).where(ShadowFeedback.thread_id == threads[3]))).scalars().one()
        old.created_at = NOW - timedelta(days=40)
        await db.commit()
    async with sessions() as db:
        found = await fb.counts(db, owner, agent.id, since=NOW - timedelta(days=28))
        assert found == {"total": 3, "by_reason": {"wrong_tone": 2, "wrong_policy": 1, "missing_context": 0, "wrong_priority": 0, "other": 0},
                         "by_kind": {"draft": 2, "ranking": 1}}
        assert await fb.count_reason(db, agent.id, "wrong_policy", NOW - timedelta(days=28)) == 1
        assert await fb.count_reason(db, agent.id, "other", NOW - timedelta(days=28)) == 0


async def test_the_lessons_view_carries_references_and_cleaned_notes_but_no_mail_text(world, fake, sessions, owner):
    agent, thread = await predicted(world, fake, sessions, owner)
    await give(sessions, owner, agent, thread, reason="wrong_policy", note="refund rule was wrong, mail me at a@b.co")
    async with sessions() as db:
        (found,) = await fb.for_lessons(db, owner, agent.id, since=NOW - timedelta(days=365))
    assert set(found) == {"thread_id", "account_id", "kind", "reason", "note", "created_at"}
    assert found["thread_id"] == thread and found["reason"] == "wrong_policy" and "a@b.co" not in found["note"]
    async with sessions() as db:
        assert await fb.for_lessons(db, owner, agent.id, since=NOW + timedelta(days=1)) == []


async def test_feedback_goes_with_the_agent(world, fake, sessions, owner):
    agent, thread = await predicted(world, fake, sessions, owner)
    await give(sessions, owner, agent, thread)
    assert len(await rows(sessions)) == 1
    async with sessions() as db:
        await db.delete(await db.get(Agent, agent.id))
        await db.commit()
    assert await rows(sessions) == []
