"""Deleting a previously viewed topic checks its last-read hash."""
from tests.routers.test_agent_memory_depth_api import (  # noqa: F401
    agent_cleanup, api, cleanup, cli, code_of, local_enabled, make, owner, rt, sessions, topic_url,
)


async def test_stale_topic_delete_preserves_newer_text(api):
    agent = await make(api)
    first = (await api.put(topic_url(agent), json={'text': 'Original'})).json()
    latest = (await api.put(topic_url(agent), json={'text': 'Newer'})).json()
    stale = await api.delete(topic_url(agent), params={'expected_hash': first['hash']})
    assert stale.status_code == 409 and code_of(stale) == 'memory_changed'
    assert (await api.get(topic_url(agent))).json()['text'] == 'Newer'
    assert (await api.delete(topic_url(agent), params={'expected_hash': latest['hash']})).status_code == 204
