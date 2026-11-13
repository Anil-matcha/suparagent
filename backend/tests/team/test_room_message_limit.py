"""The room message API's character boundary."""

from tests.team.test_rooms_router import api, harness, make_room  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401


async def test_room_message_character_limit(api, harness):
    room = await make_room(api, harness)
    url = f"/rooms/{room['id']}/messages"
    assert (await api.post(url, json={'text': '😀' * 8000})).status_code == 201
    assert (await api.post(url, json={'text': '😀' * 8001})).status_code == 422
