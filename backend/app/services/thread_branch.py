"""Branch a conversation: start a new thread from the history before one past
message, so the person can edit that message and get a different answer while
the original conversation stays as it was.

Only stored records are copied (earlier turns and their events). Workspace
files are not copied: the branch uses the same folder as its parent. Attachment
files stay with the parent thread."""

from __future__ import annotations

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select

from app.db.models.event import Event
from app.db.models.turn import Turn
from app.services import thread_store as store

# Turn columns that belong to a run in progress or to the parent's identity.
_TURN_SKIP = {"id", "thread_id", "owner_token", "owner_instance", "heartbeat_at"}
BRANCH_EVENT = "branch.context"


class BranchError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


async def branch_thread(rt, thread_id: int, turn_id: int) -> store.Thread:
    """Create a new thread holding every turn before `turn_id`. The caller sends
    the edited message into it. Raises BranchError."""
    async with rt.session() as db:
        parent = await store.get_thread(db, thread_id)
        if parent is None or parent.owner_id != rt.owner_id:
            raise store.ThreadNotFound(thread_id)
        if parent.kind != "chat":
            raise BranchError(409, "not_branchable", "Only a normal chat can be branched.")
        target = await store.get_turn(db, turn_id)
        if target is None or target.thread_id != thread_id:
            raise BranchError(404, "turn_not_found", "That message is not in this conversation.")
        earlier = list(
            (
                await db.execute(
                    select(Turn).where(Turn.thread_id == thread_id, Turn.seq < target.seq).order_by(Turn.seq)
                )
            ).scalars()
        )
        if any(turn.status == "running" for turn in earlier):
            raise BranchError(409, "turn_running", "Wait for the running answer to finish.")
        earlier_ids = {turn.id for turn in earlier}
        events = [
            event
            for event in await store.list_all_events(db, thread_id)
            if event.turn_id in earlier_ids
        ]
        parent_values = {
            "provider": parent.provider,
            "model": parent.model,
            "title": parent.title,
            "agent_id": parent.agent_id,
            "workspace_path": parent.workspace_path,
            "effort": parent.effort,
        }

    adapter = rt.adapters.get(parent_values["provider"])
    if adapter is None:
        raise BranchError(409, "unknown_provider", "This conversation's engine is not available.")
    session_id = adapter.new_session_id()
    async with rt.session() as db:
        await store.begin_write(db)
        branch = await store.create_thread(
            db,
            owner_id=rt.owner_id,
            provider_session_id=session_id,
            session_state="new" if session_id is not None else "none",
            provider_state={"branched_from": {"thread_id": thread_id, "turn_id": turn_id}},
            commit=False,
            **parent_values,
        )
        turn_map: dict[int, int] = {}
        columns = [c.key for c in sa_inspect(Turn).mapper.column_attrs if c.key not in _TURN_SKIP]
        for old in earlier:
            copy = Turn(thread_id=branch.id, **{key: getattr(old, key) for key in columns})
            db.add(copy)
            await db.flush()
            turn_map[old.id] = copy.id
        for seq, event in enumerate(events, start=1):
            db.add(
                Event(
                    thread_id=branch.id,
                    turn_id=turn_map.get(event.turn_id),
                    seq=seq,
                    type=event.type,
                    data=event.data,
                )
            )
        seq = len(events)
        if turn_map:
            # The new engine session has not seen these turns; the runtime
            # hands them over once as background (see _fallback_handoff).
            seq += 1
            db.add(
                Event(
                    thread_id=branch.id,
                    turn_id=None,
                    seq=seq,
                    type=BRANCH_EVENT,
                    data={"turn_ids": list(turn_map.values()), "from_thread_id": thread_id},
                )
            )
        branch.last_seq = seq
        await db.commit()
        await db.refresh(branch)
    return branch
