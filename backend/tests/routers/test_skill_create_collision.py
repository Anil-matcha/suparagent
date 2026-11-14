"""A new-skill save must not overwrite another window's creation."""
from tests.routers.test_agent_skills_api import (  # noqa: F401
    agent_cleanup, api, cleanup, cli, local_enabled, make, owner, put, rt, sessions, url,
)


async def test_create_only_rejects_an_existing_name_but_edits_still_work(api):
    agent = await make(api)
    assert (await put(api, agent, 'report', body='First window')).status_code == 200
    collision = await put(api, agent, 'report', body='Second window', create_only=True)
    assert collision.status_code == 409
    assert collision.json()['detail']['error_code'] == 'skill_exists'
    assert (await api.get(url(agent, 'report'))).json()['body'] == 'First window'
    assert (await put(api, agent, 'report', body='Intentional edit')).status_code == 200
    assert (await api.get(url(agent, 'report'))).json()['body'] == 'Intentional edit'
