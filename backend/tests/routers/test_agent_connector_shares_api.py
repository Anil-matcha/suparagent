"""PATCH /api/v1/agents/{id} with connector_shares: the share of a mailbox an agent may see.

Real routes, runtime and database; Composio's account list is a stand-in. The enforcement itself (what a
Gmail call returns under a share) is tested in tests/services/test_composio_gmail_share.py."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app import agent_shares
from app.db.models import Agent, AgentRevision
from app.services import composio_service, history_export
from sqlalchemy import select
from tests.routers.test_agents_api import agent_cleanup, client_for, code_of, local_enabled  # noqa: F401
from tests.routers.test_agents_manage_api import admin, made, phone  # noqa: F401
from tests.services.thread_harness import cleanup, cli, owner, rt, sessions  # noqa: F401


@pytest.fixture(autouse=True)
def composio_accounts(monkeypatch):
    async def accounts(*args):
        return [SimpleNamespace(id=name, toolkit="gmail", status="ACTIVE") for name in ("ca_a", "ca_b")]
    monkeypatch.setattr(composio_service, "accounts", accounts)


async def with_gmail(admin, *ids):
    agent = await made(admin)
    ids = ids or ("ca_a",)
    response = await admin.patch(f"/api/v1/agents/{agent['id']}",
                                 json={"connector_scopes": {"gmail": "read"}, "connector_account_scopes": {"gmail": list(ids)}})
    assert response.status_code == 200, response.text
    return agent["id"]


def url(agent_id):
    return f"/api/v1/agents/{agent_id}"


async def stored(sessions, agent_id):
    async with sessions() as db:
        return (await db.get(Agent, agent_id)).connector_shares


async def test_a_share_is_saved_and_the_response_never_carries_the_seed(admin, sessions):
    agent_id = await with_gmail(admin)
    assert (await admin.get(url(agent_id))).json()["connector_shares"] == {}
    saved = await admin.patch(url(agent_id), json={"connector_shares": {"gmail": {"ca_a": {"percent": 10}}}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["connector_shares"] == {"gmail": {"ca_a": {"percent": 10, "pins": []}}}
    seed = (await stored(sessions, agent_id))["gmail"]["ca_a"]["seed"]
    assert agent_shares.valid_seed(seed)
    for response in (await admin.get(url(agent_id)), await admin.get("/api/v1/agents")):
        assert seed not in response.text and "seed" not in response.text


async def test_raising_the_percent_keeps_the_seed(admin, sessions):
    agent_id = await with_gmail(admin)
    await admin.patch(url(agent_id), json={"connector_shares": {"gmail": {"ca_a": {"percent": 5, "pins": ["t1"]}}}})
    first = (await stored(sessions, agent_id))["gmail"]["ca_a"]
    raised = await admin.patch(url(agent_id), json={"connector_shares": {"gmail": {"ca_a": {"percent": 25}}}})
    assert raised.status_code == 200
    second = (await stored(sessions, agent_id))["gmail"]["ca_a"]
    assert second["seed"] == first["seed"] and second["percent"] == 25
    assert second["pins"] == ["t1"]  # pins not sent are kept


async def test_reshuffle_issues_a_new_seed(admin, sessions):
    agent_id = await with_gmail(admin)
    await admin.patch(url(agent_id), json={"connector_shares": {"gmail": {"ca_a": {"percent": 5}}}})
    first = (await stored(sessions, agent_id))["gmail"]["ca_a"]["seed"]
    await admin.patch(url(agent_id), json={"connector_shares": {"gmail": {"ca_a": {"percent": 5, "reshuffle": True}}}})
    assert (await stored(sessions, agent_id))["gmail"]["ca_a"]["seed"] != first


async def test_percent_100_is_kept_as_an_entry_so_it_can_be_shown(admin, sessions):
    agent_id = await with_gmail(admin)
    saved = await admin.patch(url(agent_id), json={"connector_shares": {"gmail": {"ca_a": {"percent": 100}}}})
    assert saved.json()["connector_shares"]["gmail"]["ca_a"]["percent"] == 100
    assert (await stored(sessions, agent_id))["gmail"]["ca_a"]["percent"] == 100


async def test_omitting_keeps_and_an_empty_object_or_null_clears(admin, sessions):
    agent_id = await with_gmail(admin)
    body = {"connector_shares": {"gmail": {"ca_a": {"percent": 10}}}}
    await admin.patch(url(agent_id), json=body)
    kept = await admin.patch(url(agent_id), json={"title": "Reader"})
    assert kept.json()["connector_shares"]["gmail"]["ca_a"]["percent"] == 10
    for clear in ({}, None):
        await admin.patch(url(agent_id), json=body)
        cleared = await admin.patch(url(agent_id), json={"connector_shares": clear})
        assert cleared.status_code == 200 and cleared.json()["connector_shares"] == {}
        assert await stored(sessions, agent_id) == {}


@pytest.mark.parametrize("shares", [
    {"github": {"ca_a": {"percent": 10}}},          # only Gmail in v1
    {"gmail": {"ca_other": {"percent": 10}}},       # an account the agent was not given
    {"gmail": {"ca_b": {"percent": 10}}},           # active, but not selected for this agent
])
async def test_only_gmail_and_selected_accounts_accept_a_share(admin, sessions, shares):
    agent_id = await with_gmail(admin)  # selects ca_a only
    response = await admin.patch(url(agent_id), json={"connector_shares": shares})
    assert response.status_code == 422 and code_of(response) == "invalid_connector_shares"
    assert await stored(sessions, agent_id) == {}


async def test_an_app_that_is_not_allowed_takes_no_share(admin):
    agent = await made(admin)
    response = await admin.patch(url(agent["id"]), json={"connector_shares": {"gmail": {"ca_a": {"percent": 10}}}})
    assert response.status_code == 422 and code_of(response) == "invalid_connector_shares"


@pytest.mark.parametrize("entry", [
    {"percent": 0}, {"percent": 101}, {"percent": -5}, {"percent": 5.5}, {"percent": "5"}, {},
    {"percent": 10, "seed": "0" * 32},              # a client cannot choose or read the seed
    {"percent": 10, "pins": ["x"] * 201},
    {"percent": 10, "pins": ["x" * 201]},
    {"percent": 10, "pins": [""]},
    {"percent": 10, "pins": "t1"},
])
async def test_bad_values_are_422(admin, sessions, entry):
    agent_id = await with_gmail(admin)
    response = await admin.patch(url(agent_id), json={"connector_shares": {"gmail": {"ca_a": entry}}})
    assert response.status_code == 422, response.text
    assert await stored(sessions, agent_id) == {}


async def test_a_share_is_dropped_when_its_account_is_deselected_or_the_app_is_turned_off(admin, sessions):
    agent_id = await with_gmail(admin, "ca_a", "ca_b")
    await admin.patch(url(agent_id), json={"connector_shares": {"gmail": {"ca_a": {"percent": 10}, "ca_b": {"percent": 20}}}})
    seed_a = (await stored(sessions, agent_id))["gmail"]["ca_a"]["seed"]
    await admin.patch(url(agent_id), json={"connector_account_scopes": {"gmail": ["ca_a"]}})
    left = await stored(sessions, agent_id)
    assert list(left["gmail"]) == ["ca_a"] and left["gmail"]["ca_a"]["seed"] == seed_a
    await admin.patch(url(agent_id), json={"connector_scopes": {}})
    assert await stored(sessions, agent_id) == {}


async def test_a_restricted_member_cannot_change_a_share(phone):
    agent = await made(phone)
    for body in ({"connector_shares": {"gmail": {"ca_a": {"percent": 10}}}}, {"connector_shares": {}}, {"connector_shares": None}):
        response = await phone.patch(url(agent["id"]), json=body)
        assert response.status_code == 403 and code_of(response) == "field_requires_admin"


async def test_a_share_change_is_recorded_hidden_from_clients_and_restored_with_the_same_seed(admin, sessions):
    agent_id = await with_gmail(admin)
    await admin.patch(url(agent_id), json={"connector_shares": {"gmail": {"ca_a": {"percent": 10}}}})
    seed = (await stored(sessions, agent_id))["gmail"]["ca_a"]["seed"]
    revisions = (await admin.get(f"{url(agent_id)}/revisions")).json()
    assert "connector_shares" in revisions[0]["changed_fields"]
    number = revisions[0]["number"]
    detail = await admin.get(f"{url(agent_id)}/revisions/{number}")
    assert detail.json()["snapshot"]["connector_shares"] == {"gmail": {"ca_a": {"percent": 10, "pins": []}}}
    assert seed not in detail.text
    async with sessions() as db:  # the stored snapshot keeps the seed so a restore keeps the sample
        row = (await db.execute(select(AgentRevision).where(AgentRevision.agent_id == agent_id, AgentRevision.number == number))).scalar_one()
        assert row.snapshot["connector_shares"]["gmail"]["ca_a"]["seed"] == seed
    # The entry goes away, then the earlier version is restored: same account, same sample.
    await admin.patch(url(agent_id), json={"connector_shares": {}})
    assert await stored(sessions, agent_id) == {}
    restored = await admin.post(f"{url(agent_id)}/revisions/{number}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["connector_shares"] == {"gmail": {"ca_a": {"percent": 10, "pins": []}}}
    assert (await stored(sessions, agent_id))["gmail"]["ca_a"]["seed"] == seed
    assert seed not in restored.text


def test_the_export_row_carries_no_seed():
    agent = SimpleNamespace(**{name: None for name in history_export.AGENT_FIELDS})
    agent.connector_shares = {"gmail": {"ca_a": {"percent": 5, "seed": "ab" * 16, "pins": ["t1"]}}}
    row = history_export._row(agent, history_export.AGENT_FIELDS)
    assert row["connector_shares"] == {"gmail": {"ca_a": {"percent": 5, "pins": ["t1"]}}}
    assert "ab" * 16 not in str(row)


def test_stored_shares_view_and_merge_rules():
    stored_value = {"gmail": {"ca_a": {"percent": 5, "seed": "cd" * 16, "pins": ["t1", "t2"]}}}
    assert agent_shares.public(stored_value) == {"gmail": {"ca_a": {"percent": 5, "pins": ["t1", "t2"]}}}
    assert agent_shares.public("junk") == {} and agent_shares.public({"gmail": {"ca_a": {"percent": "5"}}}) == {}
    merged = agent_shares.merge({"gmail": {"ca_a": {"percent": 8}, "ca_b": {"percent": 3}}}, stored_value)
    assert merged["gmail"]["ca_a"]["seed"] == "cd" * 16 and merged["gmail"]["ca_a"]["pins"] == ["t1", "t2"]
    assert merged["gmail"]["ca_b"]["seed"] != "cd" * 16 and agent_shares.valid_seed(merged["gmail"]["ca_b"]["seed"])
    supplied = agent_shares.merge({"gmail": {"ca_c": {"percent": 3}}}, {}, {"gmail": {"ca_c": "ef" * 16}})
    assert supplied["gmail"]["ca_c"]["seed"] == "ef" * 16
