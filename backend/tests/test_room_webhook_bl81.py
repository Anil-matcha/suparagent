"""Posting into a room by webhook (BL-81): auth, labelling, redaction, no dispatch, idempotency."""

import pytest
from fastapi import FastAPI
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.db.models import Event, Room, RoomDispatch, Thread
from app.routers.hooks import router as hooks_router
from app.services import room_webhook as rw
from app.services import routine_service as service


@pytest.fixture
async def sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'w.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def fresh_limits():
    rw._recent.clear()


async def make_room(sessions, owner="o1", archived=False):
    async with sessions() as db:
        thread = Thread(owner_id=owner, provider="claude", model=None)
        db.add(thread)
        await db.flush()
        room = Room(owner_id=owner, name="Ops", thread_id=thread.id)
        db.add(room)
        await db.commit()
        return room.id, thread.id


async def test_enable_post_and_labelled_message_without_dispatch(sessions):
    room_id, thread_id = await make_room(sessions)
    async with sessions() as db:
        secret = await rw.enable(db, "o1", room_id)
    assert secret.startswith("rm_")
    async with sessions() as db:
        seq, tid, created = await rw.accept(
            db, room_id, secret, '{"text": "deploy ok key sk-abcdefghijklmnopqrstuvwxyz123456", "sender": "  CI\\n bot "}',
            "application/json")
    assert created and tid == thread_id
    async with sessions() as db:
        event = (await db.execute(select(Event).where(Event.thread_id == thread_id))).scalar_one()
        assert event.type == "room.message" and event.seq == seq
        assert event.data["author"] == {"kind": "webhook", "label": "CI bot"}
        assert event.data["source"] == {"origin": "webhook"}
        assert "sk-abcdefghijklmnop" not in event.data["text"] and "deploy ok" in event.data["text"]
        assert (await db.execute(select(RoomDispatch))).first() is None
        stored = (await db.get(Room, room_id)).webhook_secret_hash
        assert stored and secret not in stored


async def test_wrong_secret_off_and_unknown_room_are_the_same_401(sessions):
    room_id, _ = await make_room(sessions)
    for target, secret in ((room_id, None), (room_id, "rm_nope"), (999, "rm_nope")):
        async with sessions() as db:
            with pytest.raises(rw.RoomWebhookRejected) as exc:
                await rw.accept(db, target, secret, "hi", "text/plain")
        assert exc.value.status_code == 401
    async with sessions() as db:
        secret = await rw.enable(db, "o1", room_id)
    async with sessions() as db:
        await rw.disable(db, "o1", room_id)
    async with sessions() as db:
        with pytest.raises(rw.RoomWebhookRejected) as exc:
            await rw.accept(db, room_id, secret, "hi", "text/plain")
    assert exc.value.status_code == 401


async def test_only_the_owner_can_configure(sessions):
    room_id, _ = await make_room(sessions)
    async with sessions() as db:
        with pytest.raises(rw.RoomWebhookRejected) as exc:
            await rw.enable(db, "someone-else", room_id)
    assert exc.value.status_code == 404


async def test_limits_bad_bodies_and_rate(sessions, monkeypatch):
    room_id, _ = await make_room(sessions)
    async with sessions() as db:
        secret = await rw.enable(db, "o1", room_id)

    async def post(body, ctype="text/plain", key=None):
        async with sessions() as db:
            return await rw.accept(db, room_id, secret, body, ctype, delivery_id=key)

    for body, ctype, code in (("x" * 8001, "text/plain", 413), ("   ", "text/plain", 400),
                              ("{", "application/json", 400), ('{"nope": 1}', "application/json", 400)):
        with pytest.raises(rw.RoomWebhookRejected) as exc:
            await post(body, ctype)
        assert exc.value.status_code == code
    monkeypatch.setattr(rw, "RATE_LIMIT", 2)
    await post("a")
    await post("b")
    with pytest.raises(rw.RoomWebhookRejected) as exc:
        await post("c")
    assert exc.value.status_code == 429


async def test_idempotency_key_posts_once_and_rejects_changed_body(sessions):
    room_id, thread_id = await make_room(sessions)
    async with sessions() as db:
        secret = await rw.enable(db, "o1", room_id)
    async with sessions() as db:
        first = await rw.accept(db, room_id, secret, "hello", "text/plain", delivery_id="d-1")
    async with sessions() as db:
        again = await rw.accept(db, room_id, secret, "hello", "text/plain", delivery_id="d-1")
    assert first[2] is True and again[2] is False and again[0] == first[0]
    async with sessions() as db:
        with pytest.raises(rw.RoomWebhookRejected) as exc:
            await rw.accept(db, room_id, secret, "different", "text/plain", delivery_id="d-1")
        assert exc.value.status_code == 409
        count = len((await db.execute(select(Event).where(Event.thread_id == thread_id))).scalars().all())
    assert count == 1


async def test_http_route_posts_and_refuses(sessions, monkeypatch):
    room_id, thread_id = await make_room(sessions)
    async with sessions() as db:
        secret = await rw.enable(db, "o1", room_id)

    class FakeScheduler:
        def session(self):
            return sessions()

    app = FastAPI()
    app.include_router(hooks_router)
    app.dependency_overrides[service.get_scheduler] = lambda: FakeScheduler()
    monkeypatch.setattr(settings, "ROUTINE_WEBHOOK_MAX_BYTES", 64)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        url = f"/hooks/room/{room_id}"
        assert (await client.post(url, content="hi")).status_code == 401
        assert (await client.post(url, headers={"Authorization": "Bearer bad"}, content="hi")).status_code == 401
        good = {"Authorization": f"Bearer {secret}"}
        assert (await client.post(url, headers=good, content="x" * 65)).status_code == 413
        assert (await client.post(url, headers=good, content=b"\xff\xfe")).status_code == 415
        ok = await client.post(url, headers={**good, "Idempotency-Key": "k"}, content="ping")
        assert ok.status_code == 201 and ok.headers["cache-control"] == "no-store"
        dup = await client.post(url, headers={**good, "Idempotency-Key": "k"}, content="ping")
        assert dup.status_code == 200 and dup.json() == {"seq": ok.json()["seq"], "duplicate": True}
