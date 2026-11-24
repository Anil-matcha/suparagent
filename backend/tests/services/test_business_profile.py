"""The business profile: one note about the business that every agent reads and only the person writes."""

import httpx
import pytest
from fastapi import FastAPI

from app.auth import policy
from app.core.config import settings
from app.routers.business_profile import router as profile_router
from app.services import agent_memory, business_profile, memory_journal


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_journal, "state_root", lambda: tmp_path / "state")


@pytest.fixture
async def api(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
    app = FastAPI()
    app.include_router(profile_router, prefix="/api/v1")
    transport = httpx.ASGITransport(app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        yield client


def test_empty_until_written_then_round_trips_and_trims():
    first = business_profile.read()
    assert first["empty"] is True and set(first["fields"]) == set(business_profile.FIELDS)
    saved = business_profile.write({"sells": "  Hand-poured candles\r\nSold online ", "tone": "warm"})
    assert saved["fields"]["sells"] == "Hand-poured candles\nSold online"
    assert saved["fields"]["audience"] == "" and saved["empty"] is False
    assert business_profile.read()["fields"]["tone"] == "warm"


def test_a_save_with_only_some_fields_keeps_the_others_and_empty_text_clears_one():
    business_profile.write({"sells": "candles", "tone": "warm", "hours": "9 to 5"})
    saved = business_profile.write({"hours": "10 to 4"})
    assert saved["fields"]["sells"] == "candles" and saved["fields"]["tone"] == "warm"
    assert saved["fields"]["hours"] == "10 to 4"
    cleared = business_profile.write({"tone": ""})
    assert cleared["fields"]["tone"] == "" and cleared["fields"]["sells"] == "candles"


async def test_over_http_a_partial_save_keeps_the_other_fields(api):
    await api.put("/api/v1/business-profile", json={"fields": {"sells": "candles", "tone": "warm"}})
    saved = await api.put("/api/v1/business-profile", json={"fields": {"hours": "10 to 4"}})
    assert saved.status_code == 200
    fields = saved.json()["fields"]
    assert fields["sells"] == "candles" and fields["tone"] == "warm" and fields["hours"] == "10 to 4"


def test_each_field_has_a_size_limit_and_nothing_is_stored_on_refusal():
    business_profile.write({"sells": "candles"})
    with pytest.raises(business_profile.ProfileTooLarge):
        business_profile.write({"sells": "x" * 601})
    assert business_profile.read()["fields"]["sells"] == "candles"
    with pytest.raises(business_profile.UnknownField):
        business_profile.write({"password_hint": "nope"})


def test_text_that_looks_like_a_credential_is_refused_not_stored():
    with pytest.raises(business_profile.ProfileLooksSecret) as refused:
        business_profile.write({"sells": "candles", "links": "api key sk-abcdefghijklmnopqrstuvwxyz0123456789"})
    assert refused.value.field == "links"
    with pytest.raises(business_profile.ProfileLooksSecret):
        business_profile.write({"never_say": "my token=abcdef123456 is secret"})
    assert business_profile.read()["empty"] is True


def test_a_stale_save_is_refused_with_the_current_text():
    saved = business_profile.write({"sells": "candles"})
    business_profile.write({"sells": "soap"}, expected_hash=saved["hash"])
    with pytest.raises(business_profile.ProfileConflict) as stale:
        business_profile.write({"sells": "mine"}, expected_hash=saved["hash"])
    assert stale.value.current["fields"]["sells"] == "soap"


def test_every_agents_turn_reads_it_as_background_and_an_empty_profile_adds_nothing():
    class Agent:
        id = 1
        instructions = "You are a writer."
        memory_enabled = False

    assert agent_memory.turn_instructions(Agent()) == "You are a writer."
    business_profile.write({"sells": "Hand-poured candles", "never_say": "cheap"})
    text = agent_memory.turn_instructions(Agent())
    assert text.startswith("You are a writer.")
    assert "--- BEGIN BUSINESS PROFILE ---" in text and "What the business sells: Hand-poured candles" in text
    assert "Things never to say: cheap" in text and "Who it is for" not in text
    assert "grants permission for anything" in text and "you cannot change it" in text


async def test_over_http_the_person_reads_writes_and_is_told_why_a_save_is_refused(api):
    empty = (await api.get("/api/v1/business-profile")).json()
    assert empty["empty"] is True
    saved = await api.put(
        "/api/v1/business-profile", json={"fields": {"sells": "candles"}, "expected_hash": empty["hash"]}
    )
    assert saved.status_code == 200 and saved.json()["fields"]["sells"] == "candles"
    stale = await api.put("/api/v1/business-profile", json={"fields": {"sells": "x"}, "expected_hash": empty["hash"]})
    assert stale.status_code == 409 and stale.json()["detail"]["current"]["sells"] == "candles"
    big = await api.put("/api/v1/business-profile", json={"fields": {"tone": "x" * 201}})
    assert big.status_code == 413
    secret = await api.put("/api/v1/business-profile", json={"fields": {"links": "token=abcdef123456"}})
    assert secret.status_code == 422 and secret.json()["detail"]["field"] == "links"
    unknown = await api.put("/api/v1/business-profile", json={"fields": {"other": "x"}})
    assert unknown.status_code == 422
    assert (await api.get("/api/v1/business-profile")).json()["fields"]["sells"] == "candles"
    assert policy.required_scope("PUT", "/api/v1/business-profile") == "admin"


# ---- Suggested changes (business_profile_proposals) ----

from app.services import business_profile_proposals as proposals  # noqa: E402


def _propose(field="hours", text="Open Monday to Saturday", agent_id=1, reason="the site says Saturday too"):
    return proposals.propose(field=field, text=text, reason=reason, agent_id=agent_id, agent_name="SEO writer")


def test_a_proposal_waits_and_never_changes_the_profile():
    business_profile.write({"hours": "Open Monday to Friday"})
    item = _propose()
    assert item["base_text"] == "Open Monday to Friday" and item["agent_name"] == "SEO writer"
    assert business_profile.read()["fields"]["hours"] == "Open Monday to Friday"
    assert [i["id"] for i in proposals.list_pending()] == [item["id"]]


def test_accept_applies_the_text_through_the_profile_save_and_removes_it():
    business_profile.write({"hours": "Open Monday to Friday", "sells": "candles"})
    item = _propose()
    saved = proposals.accept(item["id"])
    assert saved["fields"]["hours"] == "Open Monday to Saturday" and saved["fields"]["sells"] == "candles"
    assert proposals.list_pending() == []
    with pytest.raises(proposals.ProposalNotFound):
        proposals.accept(item["id"])


def test_accept_is_refused_when_the_field_changed_unless_the_owner_overwrites():
    business_profile.write({"hours": "Open Monday to Friday"})
    item = _propose()
    business_profile.write({"hours": "Closed on Mondays"})
    with pytest.raises(proposals.ProposalStale) as stale:
        proposals.accept(item["id"])
    assert stale.value.current == "Closed on Mondays"
    assert business_profile.read()["fields"]["hours"] == "Closed on Mondays"
    assert proposals.accept(item["id"], overwrite=True)["fields"]["hours"] == "Open Monday to Saturday"


def test_dismiss_drops_it_and_the_same_text_is_not_proposed_again():
    item = _propose()
    proposals.dismiss(item["id"])
    assert proposals.list_pending() == []
    with pytest.raises(proposals.ProposalRefused) as again:
        _propose()
    assert again.value.code == "dismissed"
    assert _propose(text="Open daily")["id"]


def test_proposals_refuse_secrets_oversize_unknown_fields_and_no_ops():
    for kwargs, code in (
        ({"text": "api key sk-abcdefghijklmnopqrstuvwxyz0123456789"}, "looks_secret"),
        ({"text": "x" * 201}, "too_large"),
        ({"field": "password_hint"}, "unknown_field"),
        ({"text": "   "}, "empty"),
        ({"reason": "r" * 201}, "reason_too_large"),
    ):
        with pytest.raises(proposals.ProposalRefused) as refused:
            _propose(**kwargs)
        assert refused.value.code == code, code
    business_profile.write({"hours": "Open daily"})
    with pytest.raises(proposals.ProposalRefused) as same:
        _propose(text="Open daily")
    assert same.value.code == "unchanged"
    assert proposals.list_pending() == []


def test_a_worker_keeps_one_proposal_per_field_and_the_queue_is_limited():
    first = _propose(text="One", agent_id=1)
    second = _propose(text="Two", agent_id=1)
    assert [i["id"] for i in proposals.list_pending()] == [second["id"]] and first["id"] != second["id"]
    for n in range(proposals.MAX_PENDING - 1):
        _propose(field="links", text=f"https://example.test/{n}", agent_id=100 + n)
    assert len(proposals.list_pending()) == proposals.MAX_PENDING
    with pytest.raises(proposals.ProposalRefused) as full:
        _propose(field="tone", text="warm", agent_id=999)
    assert full.value.code == "queue_full"
    assert len(proposals.list_pending()) == proposals.MAX_PENDING


async def test_only_the_person_can_list_accept_and_dismiss_over_http(api):
    business_profile.write({"hours": "Open Monday to Friday"})
    item = _propose()
    listed = (await api.get("/api/v1/business-profile/proposals")).json()
    assert [i["id"] for i in listed["items"]] == [item["id"]]
    accepted = await api.post(f"/api/v1/business-profile/proposals/{item['id']}/accept", json={})
    assert accepted.status_code == 200 and accepted.json()["fields"]["hours"] == "Open Monday to Saturday"
    assert (await api.post(f"/api/v1/business-profile/proposals/{item['id']}/accept", json={})).status_code == 404
    other = _propose(field="tone", text="warm")
    assert (await api.post(f"/api/v1/business-profile/proposals/{other['id']}/dismiss")).status_code == 200
    assert (await api.get("/api/v1/business-profile/proposals")).json()["items"] == []


async def test_a_stale_accept_over_http_is_a_409_with_the_current_text(api):
    business_profile.write({"hours": "Open Monday to Friday"})
    item = _propose()
    business_profile.write({"hours": "Closed on Mondays"})
    stale = await api.post(f"/api/v1/business-profile/proposals/{item['id']}/accept", json={})
    assert stale.status_code == 409 and stale.json()["detail"]["error_code"] == "proposal_stale"
    forced = await api.post(f"/api/v1/business-profile/proposals/{item['id']}/accept", json={"overwrite": True})
    assert forced.status_code == 200


def test_the_agent_tool_can_only_queue_a_proposal():
    from app.services.memory_bridge import MemoryBridge

    business_profile.write({"hours": "Open Monday to Friday"})
    agent = type("A", (), {"id": 7, "name": "Daily briefer"})()
    ok = MemoryBridge.suggest_profile_change(agent, {"field": "hours", "text": "Open daily", "reason": "site"})
    assert "owner" in ok["text"] and business_profile.read()["fields"]["hours"] == "Open Monday to Friday"
    assert proposals.list_pending()[0]["agent_name"] == "Daily briefer"
    refused = MemoryBridge.suggest_profile_change(agent, {"field": "hours", "text": "key sk-abcdefghijklmnopqrstuvwxyz0123456789", "reason": "x"})
    assert "error" in refused and len(proposals.list_pending()) == 1


def test_the_profile_tool_is_in_the_memory_server_and_takes_no_other_field():
    from app.memory_mcp_server import TOOLS
    from app.services import memory_bridge

    tool = next(t for t in TOOLS if t["name"] == "suggest_profile_change")
    assert set(tool["inputSchema"]["properties"]) == {"field", "text", "reason"}
    assert set(tool["inputSchema"]["properties"]["field"]["enum"]) == set(business_profile.FIELDS)
    assert memory_bridge.is_memory_call(server="memory", tool="suggest_profile_change")
