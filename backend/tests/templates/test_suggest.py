"""Help me choose: bundled workers ranked for stated goals, read-only."""

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.routers.templates import router as templates_router
from app.team import mcp_server, policy, service, setup
from app.templates import catalog, suggest

SUGGEST = "/api/v1/templates/suggest"
KNOWN = {t.id for t in catalog.TEMPLATES}


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


@pytest.fixture
async def api():
    app = FastAPI()
    app.include_router(templates_router, prefix="/api/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app, client=("127.0.0.1", 50000)), base_url="http://localhost"
    ) as client:
        yield client


def test_every_goal_names_only_bundled_workers():
    assert suggest.GOALS
    for key, (label, ids) in suggest.GOALS.items():
        assert label and ids, key
        assert set(ids) <= KNOWN, key


def test_a_known_goal_ranks_its_workers_first_with_a_reason():
    found = suggest.suggest(["leads"])
    assert found[0].template_id == "sales-sales"
    assert found[0].reason.startswith("Fits your goal")
    assert all(f.template_id in KNOWN for f in found)


def test_free_text_is_matched_as_words_and_nothing_stated_returns_nothing():
    assert suggest.suggest([], "") == []
    found = suggest.suggest([], "I run a small bakery and need blog articles about keywords")
    assert found and found[0].reason == "Matches what you described"
    assert suggest.suggest(["zzzz qqqq"], "") == []


def test_order_is_stable_and_limited():
    first = suggest.suggest(["research", "content", "briefing"], limit=3)
    assert len(first) == 3
    assert first == suggest.suggest(["research", "content", "briefing"], limit=3)
    assert [f.score for f in first] == sorted((f.score for f in first), reverse=True)


def test_setup_depth_cap_leaves_out_heavier_workers():
    everything = {f.template_id for f in suggest.suggest(list(suggest.GOALS), limit=8)}
    quick = {f.template_id for f in suggest.suggest(list(suggest.GOALS), max_setup="express", limit=8)}
    assert any(catalog.get(i).setup_depth != "express" for i in everything)
    assert quick and all(catalog.get(i).setup_depth == "express" for i in quick)


async def test_endpoint_returns_only_known_ids_and_creates_nothing(api):
    response = await api.post(SUGGEST, json={"goals": ["leads", "content"], "about": "B2B software"})
    assert response.status_code == 200, response.text
    items = response.json()["suggestions"]
    assert items and {i["template_id"] for i in items} <= KNOWN
    assert all(i["reason"] for i in items)
    # Read-only: asking again gives the same answer and there is no state to change.
    again = await api.post(SUGGEST, json={"goals": ["leads", "content"], "about": "B2B software"})
    assert again.json() == response.json()


async def test_endpoint_checks_its_input(api):
    assert (await api.post(SUGGEST, json={"goals": ["x"] * 9})).status_code == 422
    assert (await api.post(SUGGEST, json={"goals": ["x" * 121]})).status_code == 422
    assert (await api.post(SUGGEST, json={"about": "x" * 501})).status_code == 422
    assert (await api.post(SUGGEST, json={"max_setup": "slow"})).status_code == 422


async def test_goal_list_endpoint(api):
    response = await api.get(SUGGEST + "/goals")
    assert response.status_code == 200
    assert [g["key"] for g in response.json()] == list(suggest.GOALS)


def test_team_lead_tool_is_listed_read_only_and_validates():
    tool = next(t for t in mcp_server.TOOLS if t["name"] == "suggest_workers")
    assert tool["inputSchema"]["additionalProperties"] is False
    assert "suggest_workers" in setup.TOOLS and "suggest_workers" not in setup.WRITES
    member = policy.TurnTeam(1, 2, False, False)
    assert policy.automatic("mcp__team__suggest_workers", "plan", member, {})[0] == "allowed"
    result = setup.suggest_workers({"goals": ["briefing"]})
    assert result["suggestions"][0]["template_id"] == "operations-analyst"
    assert {s["template_id"] for s in result["suggestions"]} <= KNOWN
    for bad in ({"goals": "leads"}, {"goals": ["x"] * 9}, {"about": 5}, {"max_setup": "slow"}, {"create": True}):
        with pytest.raises(service.TeamError):
            setup.suggest_workers(bad)
