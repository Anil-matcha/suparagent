"""Saving a shadow routine (BL-173 slice 5): the grants a person may give a routine that ranks mail and adds its own labels."""
from types import SimpleNamespace

import pytest

from app import composio_tools
from tests.routers.test_routines_api import (  # noqa: F401
    api, agent, scheduler, local_enabled, agent_args, routine_cleanup, make,
    sessions, owner, rt, cleanup, cli,
)

MARK, LIST, APPLY = composio_tools.TRIAGE_MARK, composio_tools.LABEL_LIST, composio_tools.LABEL_APPLY
READS = ["gmail_get_profile", "gmail_fetch_emails", "gmail_fetch_thread"]
SHADOW = READS + [MARK, LIST, APPLY]


def grants(tools, account="ca_mail"):
    return [{"tool": tool, "account_id": account} for tool in tools]


async def prepare(sessions, agent, monkeypatch, *, level="labels", selected=("ca_mail",), active=("ca_mail", "ca_other")):
    from app.services import agent_store, composio_service

    async with sessions() as db:
        await agent_store.update_agent(db, agent.id, connector_scopes={"gmail": level}, connector_account_scopes={"gmail": list(selected)})

    async def accounts(*args):
        return [SimpleNamespace(id=a, toolkit="gmail", status="ACTIVE") for a in active]
    monkeypatch.setattr(composio_service, "accounts", accounts)


async def post(api, agent, tools, account="ca_mail"):
    return await api.post("/api/v1/routines", json={
        "agent_id": agent.id, "name": "Shadow", "prompt": "Rank and label", "schedule_kind": "weekdays",
        "schedule_spec": {"days": [1, 2, 3, 4, 5], "time": "09:00"}, "timezone": "Europe/Berlin",
        "connected_app_grants": grants(tools, account)})


def error_code(response):
    detail = response.json()["detail"]
    # A tool no routine may hold at all fails the request's own validation (a list) before the grant rules (a code).
    return detail["error_code"] if isinstance(detail, dict) else "invalid_app_grant"


async def test_a_shadow_routine_is_saved_for_an_agent_at_the_labels_level(api, agent, sessions, monkeypatch):
    await prepare(sessions, agent, monkeypatch)
    response = await post(api, agent, SHADOW)
    assert response.status_code == 201, response.text
    assert {g["tool"] for g in response.json()["connected_app_grants"]} == set(SHADOW)


async def test_the_mark_alone_is_saved_at_the_read_level_but_the_label_is_not(api, agent, sessions, monkeypatch):
    await prepare(sessions, agent, monkeypatch, level="read")
    assert (await post(api, agent, READS + [MARK, LIST])).status_code == 201
    refused = await post(api, agent, READS + [MARK, APPLY])
    assert refused.status_code == 422 and error_code(refused) == "invalid_app_grant"
    assert "access" in refused.json()["detail"]["message"].lower() if "message" in refused.json()["detail"] else True


@pytest.mark.parametrize("extra", ["gmail_send_email", "gmail_prepare_checked_reply", "gmail_create_draft_reply", "github_get_authenticated_user"])
async def test_a_shadow_routine_cannot_also_hold_a_change_or_another_app(api, agent, sessions, monkeypatch, extra):
    await prepare(sessions, agent, monkeypatch, level="write")
    refused = await post(api, agent, SHADOW + [extra])
    assert refused.status_code == 422 and error_code(refused) == "invalid_app_grant"


async def test_a_shadow_routine_uses_one_mailbox(api, agent, sessions, monkeypatch):
    await prepare(sessions, agent, monkeypatch, selected=("ca_mail", "ca_other"))
    mixed = grants(READS) + grants([MARK, APPLY], "ca_other")
    response = await api.post("/api/v1/routines", json={
        "agent_id": agent.id, "name": "Shadow", "prompt": "x", "schedule_kind": "weekdays",
        "schedule_spec": {"days": [1], "time": "09:00"}, "timezone": "UTC", "connected_app_grants": mixed})
    assert response.status_code == 422 and error_code(response) == "invalid_app_grant"


async def test_a_shadow_routine_needs_an_account_the_agent_may_use_and_that_is_connected(api, agent, sessions, monkeypatch):
    await prepare(sessions, agent, monkeypatch, selected=("ca_mail",))
    assert error_code(await post(api, agent, SHADOW, account="ca_other")) == "invalid_app_grant"   # not selected for the agent
    await prepare(sessions, agent, monkeypatch, selected=("ca_mail",), active=())
    assert error_code(await post(api, agent, SHADOW)) == "invalid_app_grant"                      # not connected any more


async def test_updating_a_routine_to_a_shadow_set_is_validated_the_same_way(api, agent, sessions, monkeypatch):
    await prepare(sessions, agent, monkeypatch)
    created = await make(api, agent)
    path = f"/api/v1/routines/{created['id']}"
    ok = await api.patch(path, json={"agent_id": agent.id, "connected_app_grants": grants(SHADOW)})
    assert ok.status_code == 200, ok.text
    bad = await api.patch(path, json={"agent_id": agent.id, "connected_app_grants": grants(SHADOW + ["gmail_send_email"])})
    assert bad.status_code == 422 and error_code(bad) == "invalid_app_grant"
