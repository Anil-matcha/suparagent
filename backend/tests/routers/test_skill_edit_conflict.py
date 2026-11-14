"""Stale skill edits must not overwrite a newer saved version."""
from tests.routers.test_agent_skills_api import (  # noqa: F401
    agent_cleanup, api, cleanup, cli, local_enabled, make, owner, put, rt, sessions, url,
)


async def test_stale_edit_is_rejected_and_current_edit_succeeds(api):
    agent = await make(api)
    first = (await put(api, agent, 'report', body='Original')).json()
    newer = await put(api, agent, 'report', body='Newer', expected_revision=first['revision'])
    assert newer.status_code == 200
    stale = await put(api, agent, 'report', body='Stale', expected_revision=first['revision'])
    assert stale.status_code == 409
    assert stale.json()['detail']['error_code'] == 'skill_changed'
    assert (await api.get(url(agent, 'report'))).json()['body'] == 'Newer'
    current = await put(api, agent, 'report', body='Current edit', expected_revision=newer.json()['revision'])
    assert current.status_code == 200
    assert (await api.get(url(agent, 'report'))).json()['body'] == 'Current edit'
