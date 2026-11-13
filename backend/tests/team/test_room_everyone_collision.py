"""Broadcast and member mentions when an agent is named Everyone."""
from sqlalchemy import select
from app.db.models import RoomDispatch
from app.services import agent_store
from tests.team.test_rooms_router import api, harness  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401


async def test_everyone_broadcast_and_explicit_member(api, harness, sessions, owner):
    _, _, _, worker = harness
    async with sessions() as db:
        named = await agent_store.create_agent(db, owner_id=owner, name='Everyone')
    response = await api.post('/rooms', json={'name': 'Desk', 'member_ids': [named.id, worker.id], 'default_responder_kind': 'mentions'})
    assert response.status_code == 201
    room_id = response.json()['id']
    sent = await api.post(f'/rooms/{room_id}/messages', json={'text': '@everyone please review'})
    assert sent.status_code == 201
    async with sessions() as db:
        targets = set((await db.scalars(select(RoomDispatch.agent_id).where(RoomDispatch.room_id == room_id, RoomDispatch.message_seq == sent.json()['seq']))).all())
    assert targets == {named.id, worker.id}
    sent = await api.post(f'/rooms/{room_id}/messages', json={'text': f'@Everyone (#{named.id}) please review'})
    assert sent.status_code == 201
    async with sessions() as db:
        targets = set((await db.scalars(select(RoomDispatch.agent_id).where(RoomDispatch.room_id == room_id, RoomDispatch.message_seq == sent.json()['seq']))).all())
    assert targets == {named.id}
