"""Room unread state exposed by the public endpoints."""
from tests.team.test_rooms_router import api, harness, make_room  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401


async def test_room_unread_is_a_boolean_and_clears_after_read(api, harness):
    room = await make_room(api, harness)
    assert room['unread'] is False
    posted = await api.post(f"/rooms/{room['id']}/messages", json={'text': 'hello'})
    assert posted.status_code == 201
    listing = (await api.get('/rooms')).json()
    assert listing[0]['unread'] is True
    assert (await api.post(f"/rooms/{room['id']}/read", json={'seq': posted.json()['seq']})).status_code == 200
    assert (await api.get('/rooms')).json()[0]['unread'] is False
