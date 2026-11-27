"""Automatic room routing (BL-42): deterministic matching, safe fallback, members only."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Agent, Room, RoomDispatch, RoomMember, Thread
from app.rooms import auto_router
from app.rooms.service import Rooms

CANDS = [
    {"id": 1, "name": "Ada", "title": "Researcher", "description": "Finds sources and summarises research papers"},
    {"id": 2, "name": "Bo", "title": "Designer", "description": "Draws logos and layouts"},
]


def test_matches_description_title_and_name():
    assert auto_router.route("can you summarise these research papers", CANDS) == 1
    assert auto_router.route("need new logos and layouts", CANDS) == 2
    assert auto_router.route("Bo what do you think of this", CANDS, last_responder=1) == 2


def test_unsure_or_tied_returns_none():
    assert auto_router.route("hello there", CANDS) is None
    assert auto_router.route("", CANDS) is None
    assert auto_router.route("anything", []) is None
    twins = [dict(CANDS[0]), dict(CANDS[0], id=3, name="Cy")]
    assert auto_router.route("research papers sources", twins) is None


def test_last_responder_breaks_a_tie_only_in_a_borderline_case():
    # last responder alone never reaches the minimum score
    assert auto_router.route("hello there", CANDS, last_responder=1) is None


@pytest.fixture
async def sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def setup_room(sessions, kind="auto", default=None, tag=""):
    async with sessions() as db:
        agents = []
        for c in CANDS:
            a = Agent(owner_id="o1", name=c["name"] + tag, title=c["title"], description=c["description"], instructions="x", provider="claude")
            db.add(a)
            agents.append(a)
        outsider = Agent(owner_id="o1", name="Zed" + tag, title="Researcher", description="research papers sources", instructions="x", provider="claude")
        db.add(outsider)
        thread = Thread(owner_id="o1", provider="room", kind="room")
        db.add(thread)
        await db.flush()
        room = Room(owner_id="o1", name="R" + tag, thread_id=thread.id, default_responder_kind=kind,
                    default_responder_agent_id=agents[default].id if default is not None else None)
        db.add(room)
        await db.flush()
        for a in agents:
            db.add(RoomMember(room_id=room.id, agent_id=a.id))
        await db.commit()
        return room.id, [a.id for a in agents], outsider.id


async def post(sessions, room_id, text):
    rooms = Rooms(SimpleNamespace(owner_id="o1", session=sessions, hub=SimpleNamespace(notify=lambda *_: None)))
    async with sessions() as db:
        room = await rooms.get(db, room_id)
        members = await rooms.members(db, room_id)
        await rooms._message(db, room, text, {"kind": "person", "label": "You"}, None, members)
        await db.commit()
    async with sessions() as db:
        return [(d.agent_id, d.reason) for d in (await db.scalars(select(RoomDispatch).where(RoomDispatch.room_id == room_id))).all()]


async def test_auto_room_routes_to_best_member_and_records_reason(sessions):
    room_id, (ada, bo), outsider = await setup_room(sessions)
    assert await post(sessions, room_id, "please summarise the research papers") == [(ada, "auto")]


async def test_unsure_falls_back_to_default_member_or_nobody(sessions):
    room_id, (ada, bo), _ = await setup_room(sessions, default=1)
    assert await post(sessions, room_id, "hello there") == [(bo, "auto_fallback")]
    room_id, _, _ = await setup_room(sessions, default=None, tag="2")
    assert await post(sessions, room_id, "hello there") == []


async def test_never_routes_to_non_members_or_held_members(sessions):
    room_id, (ada, bo), outsider = await setup_room(sessions, default=1)
    async with sessions() as db:
        member = await db.scalar(select(RoomMember).where(RoomMember.room_id == room_id, RoomMember.agent_id == ada))
        from app.rooms.service import teams
        member.held_at = teams.now()
        await db.commit()
    # Ada is held and Zed is outside the room: only Bo is eligible, and the match is too weak
    assert await post(sessions, room_id, "research papers sources") == [(bo, "auto_fallback")]


async def test_other_kinds_are_unchanged(sessions):
    room_id, (ada, bo), _ = await setup_room(sessions, kind="mentions")
    assert await post(sessions, room_id, "summarise the research papers") == []
