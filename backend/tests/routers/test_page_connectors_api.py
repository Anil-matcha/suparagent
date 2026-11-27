"""Pages calling connected-app tools they declared: grammar, declarations, gates, approval levels, limits, owners."""
import json
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import delete

from app import composio_generic
from app.db.models import Agent, SavedApp, Thread
from app.routers.page_rpc import router as page_rpc_router
from app.routers.saved_apps import router as saved_apps_router
from app.routers.threads import get_runtime
from app.services import composio_bridge, composio_service, decision_log, page_connectors, pages_bridge
from app.services import thread_store as store
from app.services.composio_client import Account, Execution, ToolInfo
from tests.routers.test_agents_api import client_for, local_enabled  # noqa: F401
from tests.services.thread_harness import cleanup, cli, new_thread, owner, rt, sessions  # noqa: F401

READ = "github:GITHUB_GET_THE_AUTHENTICATED_USER"
WRITE = "github:GITHUB_CREATE_AN_ISSUE"
GENERIC = "slack:SLACK_SEND_MESSAGE"
ISSUE = {"owner": "o", "repo": "r", "title": "t"}


# ---- grammar ----

@pytest.mark.parametrize("entry", [
    "state", READ, WRITE, GENERIC, "my-app:MY_APP_DO_IT", "_x:_X_RUN",
])
def test_grammar_accepts(entry):
    assert pages_bridge._check_common({"html": "<p>x</p>", "title": "t", "uses": [entry]}, creating=True)["uses"] == [entry]


@pytest.mark.parametrize("entry", [
    "data", "composio:COMPOSIO_SEARCH_TOOLS", "github:*", "github:GITHUB_*", "*:*", "GitHub:GITHUB_X", "github:github_x",
    "github:SLACK_POST", "github:GITHUB_MANAGE_CONNECTIONS", "github:GITHUB_WAIT_FOR_CONNECTIONS", "github:", ":GITHUB_X",
    "github:GITHUB_X:Y", "a" * 65 + ":" + "A" * 65 + "_X", "gith ub:GITHUB_X",
])
def test_grammar_rejects(entry):
    with pytest.raises(pages_bridge.PageError):
        pages_bridge._check_common({"html": "<p>x</p>", "title": "t", "uses": [entry]}, creating=True)


def test_grammar_caps_toolkits_and_tools():
    nine = [f"app{i}:APP{i}_GO" for i in range(9)]
    with pytest.raises(pages_bridge.PageError):
        pages_bridge._check_common({"html": "<p>x</p>", "title": "t", "uses": nine}, creating=True)
    eight = nine[:8]
    assert pages_bridge._check_common({"html": "<p>x</p>", "title": "t", "uses": eight}, creating=True)["uses"] == eight
    many = [f"github:GITHUB_T{i}" for i in range(25)]
    with pytest.raises(pages_bridge.PageError):
        pages_bridge._check_common({"html": "<p>x</p>", "title": "t", "uses": many}, creating=True)
    ok = ["state"] + many[:24]
    assert pages_bridge._check_common({"html": "<p>x</p>", "title": "t", "uses": ok}, creating=True)["uses"] == ok


def test_declared_connectors_groups_and_is_lenient_with_stored_values():
    uses = ["state", "legacy thing", READ, WRITE, READ, GENERIC, "github:*", "composio:COMPOSIO_X", 5, None]
    assert pages_bridge.declared_connectors(uses) == [
        {"toolkit": "github", "tools": ["GITHUB_GET_THE_AUTHENTICATED_USER", "GITHUB_CREATE_AN_ISSUE"]},
        {"toolkit": "slack", "tools": ["SLACK_SEND_MESSAGE"]},
    ]
    assert pages_bridge.declared_connectors(None) == [] and pages_bridge.declared_connectors(["state"]) == []
    assert len(pages_bridge.declared_connectors([f"a{i}:A{i}_X" for i in range(20)])) == 8


# ---- the routes ----

class FakeClient:
    accounts_found = [Account(id="ca_gh", toolkit="github", alias=None, status="ACTIVE", created_at=None, updated_at=None),
                      Account(id="ca_sl", toolkit="slack", alias=None, status="ACTIVE", created_at=None, updated_at=None)]
    executed: list = []
    data: object = {"login": "octo"}
    slack_schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def __init__(self, key):
        self.key = key

    async def accounts(self, user_id, toolkit=None):
        return [a for a in self.accounts_found if toolkit is None or a.toolkit == toolkit]

    async def tool(self, slug):
        return ToolInfo(slug=slug, name=slug, description="", toolkit=slug.split("_")[0].lower(), version="1",
                        schema=self.slack_schema, tags=(), deprecated=False)

    async def create_session(self, user_id):
        return "sess_1"

    async def execute(self, session_id, slug, arguments, account_id):
        self.executed.append((slug, arguments, account_id))
        return Execution(result_type="completed", data=type(self).data, error=None)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    async def load(db, owner_id):
        return {"api_key": "ak_TESTKEY_abcdef123456", "user_id": "u1", "session_id": "sess_1"}
    monkeypatch.setattr(composio_service, "load", load)
    monkeypatch.setattr(composio_bridge, "make_client", FakeClient)
    monkeypatch.setattr(decision_log, "record", lambda event, **kw: logged.append((event, kw)))
    FakeClient.executed = []
    FakeClient.data = {"login": "octo"}
    page_connectors.reset()
    logged.clear()


logged: list = []


@pytest.fixture
async def api(rt, cli):
    app = FastAPI()
    app.include_router(saved_apps_router, prefix="/api/v1")
    app.include_router(page_rpc_router, prefix="/api/v1")
    app.dependency_overrides[get_runtime] = lambda: rt
    async with client_for(app) as client:
        yield client


@pytest.fixture(autouse=True)
async def db_cleanup(sessions, owner):
    yield
    async with sessions() as db:
        await db.execute(delete(SavedApp).where(SavedApp.owner_id.like("od_rt_%")))
        await db.execute(delete(Agent).where(Agent.owner_id.like("od_rt_%")))
        await db.commit()


async def make_agent(sessions, owner, *, mode="ask", scopes=None, accounts=None):
    async with sessions() as db:
        agent = Agent(owner_id=owner, name="Pagey " + uuid4().hex[:8], instructions="x", provider="claude", approval_mode=mode,
                      connector_scopes={"github": "write", "slack": "write"} if scopes is None else scopes,
                      connector_account_scopes={"github": ["ca_gh"], "slack": ["ca_sl"]} if accounts is None else accounts)
        db.add(agent)
        await db.commit()
        return agent.id


async def page(sessions, owner, uses, **agent):
    agent_id = await make_agent(sessions, owner, **agent) if agent is not None else None
    thread = await new_thread(sessions, owner, agent_id=agent_id)
    async with sessions() as db:
        await store.append_event(db, thread.id, "page.shown", {
            "page_id": "pg1", "rev": 1, "title": "P", "html": "<p>x</p>", "height": 360, "frameless": False, "uses": uses})
    return thread


def rpc_url(thread):
    return f"/api/v1/threads/{thread.id}/pages/pg1/rpc"


async def call(api, url, tool="GITHUB_GET_THE_AUTHENTICATED_USER", toolkit="github", engaged=False, arguments=None, **more):
    return await api.post(url, json={"method": "connectors/call", "engaged": engaged,
                                     "params": {"toolkit": toolkit, "tool": tool, "arguments": arguments or {}}, **more})


def code(response):
    return response.json()["detail"]["error_code"]


async def test_list_shows_only_declared_toolkits_with_connection_state(api, sessions, owner, monkeypatch):
    thread = await page(sessions, owner, ["state", READ, WRITE, "gmail:GMAIL_GET_PROFILE", "bogus"])
    monkeypatch.setattr(FakeClient, "accounts_found", [a for a in FakeClient.accounts_found if a.toolkit == "github"])
    response = await api.post(rpc_url(thread), json={"method": "connectors/list", "engaged": False})
    body = response.json()
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert [(c["toolkit"], c["connected"]) for c in body["connectors"]] == [("github", True), ("gmail", False)]
    assert body["connectors"][0]["label"] == "Github" and body["connectors"][0]["allowed"] is True
    assert body["connectors"][0]["tools"] == [{"tool": "GITHUB_GET_THE_AUTHENTICATED_USER", "mutating": False},
                                              {"tool": "GITHUB_CREATE_AN_ISSUE", "mutating": True}]
    assert body["connectors"][1]["allowed"] is False


async def test_a_declared_read_runs_without_a_click_and_is_audited(api, sessions, owner):
    thread = await page(sessions, owner, [READ])
    response = await call(api, rpc_url(thread))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "octo" in json.dumps(response.json()["result"])
    assert FakeClient.executed and FakeClient.executed[0][0] == "GITHUB_GET_THE_AUTHENTICATED_USER"
    assert FakeClient.executed[0][2] == "ca_gh"
    assert logged[-1][0] == "auto_allowed" and logged[-1][1]["tool"] == "mcp__composio__github_get_authenticated_user"


async def test_an_undeclared_tool_is_refused_and_the_message_names_it(api, sessions, owner):
    thread = await page(sessions, owner, [READ])
    response = await call(api, rpc_url(thread), tool="GITHUB_LIST_COMMITS", arguments={"owner": "o", "repo": "r"})
    assert response.status_code == 409 and code(response) == "not_declared"
    assert "github:GITHUB_LIST_COMMITS" in response.json()["detail"]["message"]
    assert (await call(api, rpc_url(thread), toolkit="slack", tool="SLACK_SEND_MESSAGE", engaged=True)).status_code == 409
    assert FakeClient.executed == []


async def test_a_legacy_page_without_connectors_declares_nothing(api, sessions, owner):
    thread = await page(sessions, owner, ["state", "something old"])
    assert code(await call(api, rpc_url(thread))) == "not_declared"


async def test_a_room_thread_is_refused(api, sessions, owner):
    thread = await page(sessions, owner, [READ])
    async with sessions() as db:
        (await db.get(Thread, thread.id)).room_id = 12345
        try:
            await db.commit()
        except Exception:
            pytest.skip("room rows need a room")
    response = await call(api, rpc_url(thread))
    assert response.status_code == 409 and code(response) == "room_thread"


async def test_not_connected_and_ungranted_are_readable_conflicts(api, sessions, owner, monkeypatch):
    thread = await page(sessions, owner, [READ])
    monkeypatch.setattr(FakeClient, "accounts_found", [])
    response = await call(api, rpc_url(thread))
    assert response.status_code == 409 and code(response) == "not_connected" and "connected" in response.json()["detail"]["message"]
    no_grant = await page(sessions, owner, [READ], scopes={})
    response = await call(api, rpc_url(no_grant))
    assert response.status_code == 409 and code(response) == "not_granted"
    no_account = await page(sessions, owner, [READ], accounts={})
    assert code(await call(api, rpc_url(no_account))) == "not_granted"
    no_agent = await new_thread(sessions, owner)
    async with sessions() as db:
        await store.append_event(db, no_agent.id, "page.shown", {"page_id": "pg1", "rev": 1, "title": "P", "html": "<p>x</p>", "uses": [READ]})
    assert code(await call(api, rpc_url(no_agent))) == "no_agent"
    assert FakeClient.executed == []


async def test_a_change_needs_the_click_flag_then_the_approval_the_agent_level_asks_for(api, sessions, owner):
    thread = await page(sessions, owner, [WRITE])
    url = rpc_url(thread)
    first = await call(api, url, tool="GITHUB_CREATE_AN_ISSUE", arguments=ISSUE)
    assert first.status_code == 409 and code(first) == "needs_engagement"
    asked = await call(api, url, tool="GITHUB_CREATE_AN_ISSUE", arguments=ISSUE, engaged=True)
    assert asked.status_code == 409 and code(asked) == "needs_approval"
    detail = asked.json()["detail"]
    assert "GITHUB_CREATE_AN_ISSUE" in detail["summary"] and detail["confirmation"]
    assert FakeClient.executed == []
    # The token belongs to these exact arguments; other arguments are asked again.
    other = await call(api, url, tool="GITHUB_CREATE_AN_ISSUE", arguments={**ISSUE, "title": "other"}, engaged=True,
                       confirmation=detail["confirmation"])
    assert code(other) == "needs_approval"
    # Confirming without the click flag still refuses.
    asked = await call(api, url, tool="GITHUB_CREATE_AN_ISSUE", arguments=ISSUE, engaged=True)
    token = asked.json()["detail"]["confirmation"]
    assert code(await call(api, url, tool="GITHUB_CREATE_AN_ISSUE", arguments=ISSUE, confirmation=token)) == "needs_engagement"
    done = await call(api, url, tool="GITHUB_CREATE_AN_ISSUE", arguments=ISSUE, engaged=True, confirmation=token)
    assert done.status_code == 200, done.text
    assert [e[0] for e in FakeClient.executed] == ["GITHUB_CREATE_AN_ISSUE"]
    assert logged[-1][1]["source"] == "page_confirmed"
    # One use only.
    again = await call(api, url, tool="GITHUB_CREATE_AN_ISSUE", arguments=ISSUE, engaged=True, confirmation=token)
    assert code(again) == "needs_approval"


@pytest.mark.parametrize("mode", ["ask", "accept_edits", "approve_for_me", "full_access"])
async def test_every_mode_that_asks_a_turn_to_confirm_a_change_asks_the_page_too(api, sessions, owner, mode):
    thread = await page(sessions, owner, [WRITE], mode=mode)
    response = await call(api, rpc_url(thread), tool="GITHUB_CREATE_AN_ISSUE", arguments=ISSUE, engaged=True)
    assert code(response) == "needs_approval" and FakeClient.executed == []


async def test_plan_mode_and_a_read_only_level_refuse_a_change(api, sessions, owner):
    plan = await page(sessions, owner, [WRITE, READ], mode="plan")
    response = await call(api, rpc_url(plan), tool="GITHUB_CREATE_AN_ISSUE", arguments=ISSUE, engaged=True)
    assert response.status_code == 409 and code(response) == "not_allowed" and "plan mode" in response.json()["detail"]["message"]
    reading = await page(sessions, owner, [WRITE], scopes={"github": "read"})
    response = await call(api, rpc_url(reading), tool="GITHUB_CREATE_AN_ISSUE", arguments=ISSUE, engaged=True)
    assert code(response) == "not_allowed" and "only read" in response.json()["detail"]["message"]
    assert FakeClient.executed == []
    # Plan mode still reads.
    assert (await call(api, rpc_url(plan))).status_code == 200


async def test_a_generic_action_is_a_change_and_runs_after_the_same_gates(api, sessions, owner):
    thread = await page(sessions, owner, [GENERIC])
    url = rpc_url(thread)
    args = {"text": "hi"}
    body = dict(toolkit="slack", tool="SLACK_SEND_MESSAGE", arguments=args)
    assert code(await call(api, url, **body)) == "needs_engagement"
    token = (await call(api, url, engaged=True, **body)).json()["detail"]["confirmation"]
    done = await call(api, url, engaged=True, confirmation=token, **body)
    assert done.status_code == 200, done.text
    assert FakeClient.executed == [("SLACK_SEND_MESSAGE", args, "ca_sl")]
    # Arguments are checked against the action's schema like a turn's.
    bad = await call(api, url, engaged=True, confirmation=(await call(api, url, engaged=True, toolkit="slack", tool="SLACK_SEND_MESSAGE", arguments={"nope": 1})).json()["detail"]["confirmation"],
                     toolkit="slack", tool="SLACK_SEND_MESSAGE", arguments={"nope": 1})
    assert bad.status_code == 422 and len(FakeClient.executed) == 1


async def test_tools_that_record_in_the_app_database_are_not_reachable(api, sessions, owner):
    thread = await page(sessions, owner, ["gmail:GMAIL_TRIAGE_MARK", "gmail:GMAIL_READ_SENT_REPLIES"])
    for tool in ("GMAIL_TRIAGE_MARK", "GMAIL_READ_SENT_REPLIES"):
        response = await call(api, rpc_url(thread), toolkit="gmail", tool=tool)
        assert response.status_code == 409 and code(response) == "not_available"


async def test_argument_size_error_text_and_secrets(api, sessions, owner):
    thread = await page(sessions, owner, [READ])
    big = await call(api, rpc_url(thread), arguments={"x": "a" * 70_000})
    assert big.status_code == 413 and code(big) == "arguments_too_large"
    FakeClient.data = {"login": "octo", "bio": "token ghp_" + "A" * 30}
    response = await call(api, rpc_url(thread))
    text = json.dumps(response.json())
    assert "ghp_" + "A" * 30 not in text and "[redacted]" in text


async def test_result_is_cut_at_512kb(api, sessions, owner, monkeypatch):
    out = page_connectors._bounded({"text": "word " * 120_000})
    assert out["truncated"] is True and len(json.dumps(out["result"])) <= 524_288 + 20
    assert "truncated" not in page_connectors._bounded({"a": 1})

    async def fake_run(*a, **k):
        return {"error": "ghp_" + "B" * 30 + " " + "e" * 3000, "code": "composio_unavailable"}
    monkeypatch.setattr(page_connectors, "_run", fake_run)
    thread = await page(sessions, owner, [READ])
    response = await call(api, rpc_url(thread))
    message = response.json()["detail"]["message"]
    assert response.status_code == 502 and len(message) <= 1000 and "ghp_" not in message


async def test_timeout_is_reported(api, sessions, owner, monkeypatch):
    import asyncio

    async def slow(*a, **k):
        await asyncio.sleep(5)
    monkeypatch.setattr(page_connectors, "_run", slow)
    monkeypatch.setattr(page_connectors, "TIMEOUT_SECONDS", 0.05)
    thread = await page(sessions, owner, [READ])
    response = await call(api, rpc_url(thread))
    assert response.status_code == 504 and code(response) == "timeout"


async def test_rate_limits_per_page_and_per_agent_and_toolkit(api, sessions, owner):
    thread = await page(sessions, owner, [READ])
    for _ in range(page_connectors.PAGE_LIMIT):
        assert (await call(api, rpc_url(thread))).status_code == 200
    limited = await call(api, rpc_url(thread))
    assert limited.status_code == 429 and code(limited) == "rate_limited"
    # Another page of the same agent still has room; the agent-and-toolkit window is shared.
    async with sessions() as db:
        agent_id = (await db.get(Thread, thread.id)).agent_id
    other = await new_thread(sessions, owner, agent_id=agent_id)
    async with sessions() as db:
        await store.append_event(db, other.id, "page.shown", {"page_id": "pg1", "rev": 1, "title": "P", "html": "<p>x</p>", "uses": [READ]})
    for _ in range(page_connectors.AGENT_TOOLKIT_LIMIT - page_connectors.PAGE_LIMIT):
        assert (await call(api, rpc_url(other))).status_code == 200
    assert code(await call(api, rpc_url(other))) == "rate_limited"


async def test_a_saved_app_rederives_uses_from_its_stored_version(api, sessions, owner):
    thread = await page(sessions, owner, [READ])
    item = (await api.post("/api/v1/saved-apps", json={"thread_id": thread.id, "page_id": "pg1"})).json()["app"]
    url = f"/api/v1/saved-apps/{item['id']}/rpc"
    assert (await call(api, url)).status_code == 200
    assert code(await call(api, url, tool="GITHUB_LIST_COMMITS")) == "not_declared"
    # A client cannot widen it: uses are not part of the body.
    wide = await api.post(url, json={"method": "connectors/call", "engaged": True, "uses": [WRITE],
                                     "params": {"toolkit": "github", "tool": "GITHUB_CREATE_AN_ISSUE", "arguments": ISSUE}})
    assert wide.status_code == 422
    # A new revision that declares more is followed; pinning the old version drops it again.
    async with sessions() as db:
        await svc_append(db, owner, {"page_id": "pg1", "rev": 2, "title": "P", "html": "<p>2</p>", "height": 360, "uses": [READ, WRITE]})
    listed = (await api.post(url, json={"method": "connectors/list"})).json()["connectors"][0]["tools"]
    assert len(listed) == 2
    assert (await api.patch(f"/api/v1/saved-apps/{item['id']}", json={"pinned_rev": 1})).status_code == 200
    listed = (await api.post(url, json={"method": "connectors/list"})).json()["connectors"][0]["tools"]
    assert len(listed) == 1


async def svc_append(db, owner, data):
    from app.services import saved_apps
    return await saved_apps.append_version(db, owner, data["page_id"], data)


async def test_another_owner_gets_404_and_an_unknown_page_too(api, rt, sessions, owner):
    thread = await page(sessions, owner, [READ])
    item = (await api.post("/api/v1/saved-apps", json={"thread_id": thread.id, "page_id": "pg1"})).json()["app"]
    assert (await call(api, f"/api/v1/threads/{thread.id}/pages/nope/rpc")).status_code == 404
    rt.owner_id = "od_rt_someone_else"
    assert (await call(api, rpc_url(thread))).status_code == 404
    assert (await call(api, f"/api/v1/saved-apps/{item['id']}/rpc")).status_code == 404
    rt.owner_id = owner
    assert (await call(api, f"/api/v1/saved-apps/{item['id']}/rpc")).status_code == 200


async def test_an_agent_that_was_archived_loses_the_pages_access(api, sessions, owner):
    from datetime import UTC, datetime
    thread = await page(sessions, owner, [READ])
    async with sessions() as db:
        (await db.get(Agent, (await db.get(Thread, thread.id)).agent_id)).archived_at = datetime.now(UTC)
        await db.commit()
    assert code(await call(api, rpc_url(thread))) == "no_agent"
