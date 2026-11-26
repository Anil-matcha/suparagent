"""Seeing and undoing the labels a worker applied (BL-173 slice 4): real runtime, real Composio client against a fake.

The rows are inserted directly; the tools that write them are tested elsewhere. What matters here is exactly what an
undo sends to Gmail, what it refuses to send, and that it can be repeated."""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from itertools import chain, repeat

import httpx
import pytest
from sqlalchemy import delete, select

from app.db.models import Agent, WorkerLabel, WorkerLabelAction
from app.services import agent_store, composio_bridge, composio_service, worker_labels
from app.services.composio_client import ComposioClient
from tests.services.test_composio_bridge import KEY, world  # noqa: F401
from tests.services.test_composio_gmail_share import ACCOUNT, GmailFake, entry, gmail_account, grant
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401

SLUG = "GMAIL_MODIFY_THREAD_LABELS"
GONE = "Requested entity was not found."


class LabelFake(GmailFake):
    """GmailFake that also answers label changes, and can fail, vanish or lose its session on cue."""

    def __init__(self):
        super().__init__()
        self.fail: dict[str, str] = {}        # thread id -> the error text of a failed answer
        self.forget_session = False            # session 'sess_1' answers 404 until it is replaced

    def label_calls(self):
        return [e for e in self.executes if e.get("slug") == SLUG]

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:  # the shared Gmail fake answers some routes itself, before it looks at this switch
            raise httpx.ConnectError("down " + KEY)
        if request.url.path.endswith("/execute"):
            if self.forget_session and "/sess_1/" in request.url.path:
                return httpx.Response(404, json={"error": {"message": "no session"}})
            body = json.loads(request.content)
            if body["tool_slug"] == SLUG and body["arguments"].get("thread_id") in self.fail:
                self.executes.append({"slug": SLUG, "arguments": body["arguments"], "account": body.get("account")})
                return httpx.Response(200, json={"result_type": "failed", "data": None, "error": self.fail[body["arguments"]["thread_id"]]})
        return super().handler(request)

    def gmail(self, slug, args):
        if slug == SLUG:
            return {"id": args.get("thread_id")}
        return super().gmail(slug, args)


@pytest.fixture
async def fake(monkeypatch):
    fake = LabelFake()
    monkeypatch.setattr(composio_bridge, "make_client", lambda key: ComposioClient(key, transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture(autouse=True)
async def quick(monkeypatch, sessions):
    monkeypatch.setattr(worker_labels, "PAUSE_SECONDS", 0)
    worker_labels._locks.clear()

    async def wipe():  # label definitions carry no foreign key, so nothing else clears them between tests
        async with sessions() as db:
            await db.execute(delete(WorkerLabelAction))
            await db.execute(delete(WorkerLabel))
            await db.commit()

    await wipe()
    yield
    await wipe()


async def setup(world, fake, sessions, owner, *, shares=None, labels=(("Worker/Urgent", "Label_1"), ("Worker/Bug", "Label_2"))):
    """A Gmail-connected agent and the label definitions the app made for the account."""
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, shares if shares is not None else {})
    async with sessions() as db:
        for name, label_id in labels:
            db.add(WorkerLabel(owner_id=owner, account_id=ACCOUNT, name=name, gmail_label_id=label_id))
        await db.commit()
    return runtime, agent


async def add(sessions, owner, agent_id, thread, name="Worker/Urgent", label_id="Label_1", *, account=ACCOUNT, status="applied", owner_id=None, at=None):
    async with sessions() as db:
        row = WorkerLabelAction(owner_id=owner_id or owner, agent_id=agent_id, account_id=account, thread_id=thread, label_name=name,
                                gmail_label_id=label_id, status=status, applied_at=at or datetime.now(UTC))
        db.add(row)
        await db.commit()
        return row.id


async def statuses(sessions, agent_id=None):
    async with sessions() as db:
        query = select(WorkerLabelAction.thread_id, WorkerLabelAction.status, WorkerLabelAction.undone_at).order_by(WorkerLabelAction.id)
        if agent_id is not None:
            query = query.where(WorkerLabelAction.agent_id == agent_id)
        return [(t, s, u) for t, s, u in (await db.execute(query)).all()]


async def test_undo_removes_exactly_the_recorded_labels_and_nothing_else(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner)
    await add(sessions, owner, agent.id, "th1", "Worker/Urgent", "Label_1")
    await add(sessions, owner, agent.id, "th2", "Worker/Urgent", "Label_1")
    await add(sessions, owner, agent.id, "th3", "Worker/Bug", "Label_2")
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 3, "already_removed": 0, "failed": 0}
    calls = fake.label_calls()
    assert [c["arguments"] for c in calls] == [
        {"user_id": "me", "thread_id": "th1", "remove_label_ids": ["Label_1"]},
        {"user_id": "me", "thread_id": "th2", "remove_label_ids": ["Label_1"]},
        {"user_id": "me", "thread_id": "th3", "remove_label_ids": ["Label_2"]}]
    assert all("add_label_ids" not in c["arguments"] and c["account"] == ACCOUNT for c in calls)
    assert not [e for e in fake.executes if "DELETE" in str(e.get("slug")) or "TRASH" in str(e.get("slug"))]
    assert [(t, s, u is not None) for t, s, u in await statuses(sessions)] == [("th1", "undone", True), ("th2", "undone", True), ("th3", "undone", True)]
    async with sessions() as db:  # the label definitions are never touched
        assert (await db.execute(select(WorkerLabel.name))).scalars().all().__len__() == 2


async def test_undo_is_idempotent_and_resumable(world, fake, sessions, owner, monkeypatch):
    runtime, agent = await setup(world, fake, sessions, owner)
    for n in range(5):
        await add(sessions, owner, agent.id, f"th{n}")
    monkeypatch.setattr(worker_labels, "MAX_PER_REQUEST", 2)
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 2, "already_removed": 0, "failed": 0}
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 2, "already_removed": 0, "failed": 0}
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 1, "already_removed": 0, "failed": 0}
    calls = len(fake.label_calls())
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 0, "already_removed": 0, "failed": 0}
    assert len(fake.label_calls()) == calls == 5  # nothing is removed twice


async def test_a_failed_removal_stays_applied_and_is_retried(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner)
    for t in ("th1", "th2", "th3"):
        await add(sessions, owner, agent.id, t)
    fake.fail["th2"] = "Gmail said no for some reason"
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 2, "already_removed": 0, "failed": 1}
    assert [(t, s) for t, s, _ in await statuses(sessions)] == [("th1", "undone"), ("th2", "applied"), ("th3", "undone")]
    fake.fail.clear()
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 1, "already_removed": 0, "failed": 0}
    assert {s for _, s, _ in await statuses(sessions)} == {"undone"}


async def test_already_gone_counts_as_removed(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner)
    await add(sessions, owner, agent.id, "th1")
    await add(sessions, owner, agent.id, "th2")
    fake.fail["th1"] = GONE
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 1, "already_removed": 1, "failed": 0}
    assert {s for _, s, _ in await statuses(sessions)} == {"undone"}


async def test_undo_ignores_the_share_but_not_the_account_selection(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner, shares={"gmail": {ACCOUNT: entry(1)}})  # a 1% share hides nearly all threads
    await add(sessions, owner, agent.id, "th-outside-the-share")
    await add(sessions, owner, agent.id, "th-other-account", account="ca_not_selected")
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 1, "already_removed": 0, "failed": 1}
    assert [c["arguments"]["thread_id"] for c in fake.label_calls()] == ["th-outside-the-share"]  # no call for the lost account
    assert dict((t, s) for t, s, _ in await statuses(sessions)) == {"th-outside-the-share": "undone", "th-other-account": "applied"}


async def test_gmail_switched_off_for_the_agent_refuses_every_removal(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner)
    await add(sessions, owner, agent.id, "th1")
    async with sessions() as db:
        await agent_store.update_agent(db, agent.id, connector_scopes={}, connector_account_scopes={})
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 0, "already_removed": 0, "failed": 1}
    assert fake.label_calls() == []


async def test_a_label_that_is_not_recorded_as_ours_is_never_touched(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner)
    await add(sessions, owner, agent.id, "th1", "Worker/Urgent", "Label_FOREIGN")   # same name, a different id
    await add(sessions, owner, agent.id, "th2", "Worker/Mine", "Label_1")            # not a definition we made
    await add(sessions, owner, agent.id, "th3", "Important", "IMPORTANT")            # outside the prefix
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 0, "already_removed": 0, "failed": 3}
    assert fake.label_calls() == []


async def test_undo_never_touches_other_agents_or_owners_rows(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner)
    async with sessions() as db:
        other = await agent_store.create_agent(db, owner_id=owner, name="Other")
        theirs = await agent_store.create_agent(db, owner_id="someone-else", name="Theirs")
    try:
        await add(sessions, owner, agent.id, "mine")
        await add(sessions, owner, other.id, "same-owner-other-agent")
        await add(sessions, "someone-else", theirs.id, "other-owner", owner_id="someone-else")
        assert await worker_labels.undo(runtime, agent.id) == {"removed": 1, "already_removed": 0, "failed": 0}
        assert [c["arguments"]["thread_id"] for c in fake.label_calls()] == ["mine"]
        assert dict((t, s) for t, s, _ in await statuses(sessions)) == {"mine": "undone", "same-owner-other-agent": "applied", "other-owner": "applied"}
        assert await worker_labels.undo(runtime, theirs.id) is None          # not this owner's agent
        assert await worker_labels.summary(runtime, theirs.id) is None
    finally:
        async with sessions() as db:
            await db.execute(delete(Agent).where(Agent.id.in_([other.id, theirs.id])))
            await db.commit()


async def test_only_applied_rows_are_considered(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner)
    for status in ("undone", "failed", "outcome_unknown"):
        await add(sessions, owner, agent.id, f"th-{status}", status=status)
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 0, "already_removed": 0, "failed": 0}
    assert fake.label_calls() == []


async def test_undo_cap_and_budget(world, fake, sessions, owner, monkeypatch):
    runtime, agent = await setup(world, fake, sessions, owner)
    for n in range(5):
        await add(sessions, owner, agent.id, f"th{n}")
    monkeypatch.setattr(worker_labels, "MAX_PER_REQUEST", 3)
    assert (await worker_labels.undo(runtime, agent.id))["removed"] == 3                    # the cap
    # The budget: the clock reads 0 twice (the deadline, then the first action), then jumps past the deadline.
    ticks = chain([0, 0], repeat(10_000))
    monkeypatch.setattr(worker_labels, "_clock", lambda: next(ticks))
    assert (await worker_labels.undo(runtime, agent.id))["removed"] == 1
    assert [s for _, s, _ in await statuses(sessions)].count("applied") == 1               # the rest stays for the next request


async def test_no_key_or_no_connection_makes_no_call(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner)
    await add(sessions, owner, agent.id, "th1")
    fake.down = True
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 0, "already_removed": 0, "failed": 1}
    fake.down = False
    fake.accounts.clear()                                                                   # the account is no longer connected
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 0, "already_removed": 0, "failed": 1}
    assert fake.label_calls() == []
    async with sessions() as db:
        await composio_service.remove_key(db, owner)
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 0, "already_removed": 0, "failed": 1}
    assert {s for _, s, _ in await statuses(sessions)} == {"applied"}


async def test_a_forgotten_session_is_replaced_once(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner)
    await add(sessions, owner, agent.id, "th1")
    await add(sessions, owner, agent.id, "th2")
    fake.forget_session = True
    assert await worker_labels.undo(runtime, agent.id) == {"removed": 2, "already_removed": 0, "failed": 0}
    async with sessions() as db:
        assert (await composio_service.load(db, owner))["session_id"] == "sess_new"


async def test_two_undos_at_once_remove_each_label_once(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner)
    for n in range(4):
        await add(sessions, owner, agent.id, f"th{n}")
    first, second = await asyncio.gather(worker_labels.undo(runtime, agent.id), worker_labels.undo(runtime, agent.id))
    assert first["removed"] + second["removed"] == 4 and len(fake.label_calls()) == 4


async def test_list_counts_and_breakdown(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner)
    now = datetime.now(UTC)
    await add(sessions, owner, agent.id, "th1", "Worker/Urgent", "Label_1", at=now - timedelta(hours=3))
    await add(sessions, owner, agent.id, "th2", "Worker/Urgent", "Label_1", at=now - timedelta(hours=2))
    await add(sessions, owner, agent.id, "th2", "Worker/Bug", "Label_2", at=now - timedelta(hours=1))   # same thread, a second label
    await add(sessions, owner, agent.id, "th4", "Worker/Bug", "Label_2", account="ca_not_selected", at=now - timedelta(minutes=30))
    await add(sessions, owner, agent.id, "th5", status="undone")                                        # not counted
    found = await worker_labels.summary(runtime, agent.id)
    assert found["agent_id"] == agent.id and found["applied"] == 4 and found["threads"] == 3
    assert found["labels"] == [{"name": "Worker/Bug", "count": 2}, {"name": "Worker/Urgent", "count": 2}]
    assert found["accounts"] == sorted([ACCOUNT, "ca_not_selected"])
    assert found["undoable"] == 3                                  # the unselected account's label cannot be removed
    assert datetime.fromisoformat(found["last_applied_at"]) == now - timedelta(minutes=30)   # the newest applied row, whatever its account
    fake.down = True                                               # when Composio cannot say, none is offered
    assert (await worker_labels.summary(runtime, agent.id))["undoable"] == 0


async def test_an_agent_with_nothing_applied_has_an_empty_summary(world, fake, sessions, owner):
    runtime, agent = await setup(world, fake, sessions, owner)
    assert await worker_labels.summary(runtime, agent.id) == {
        "agent_id": agent.id, "applied": 0, "threads": 0, "labels": [], "last_applied_at": None, "undoable": 0, "accounts": []}
