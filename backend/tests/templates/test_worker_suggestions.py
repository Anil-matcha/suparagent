"""The lead's latest worker suggestion is remembered for the Choose your team screen."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.routers.templates import router as templates_router
from app.services import memory_journal, worker_suggestions
from app.team import setup
from app.templates import catalog

LATEST = "/api/v1/templates/suggest/latest"
KNOWN = {t.id for t in catalog.TEMPLATES}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_journal, "state_root", lambda: tmp_path / "state")
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


def some_ids(count):
    return sorted(KNOWN)[:count]


def test_nothing_is_remembered_until_the_lead_suggests():
    assert worker_suggestions.latest() is None


def test_record_keeps_only_known_ids_once_each_and_at_most_eight():
    ids = some_ids(12)
    rows = [{"template_id": "not-a-worker", "reason": "x"}, {"template_id": ids[0], "reason": "a"}, {"template_id": ids[0], "reason": "b"}]
    rows += [{"template_id": i, "reason": "r"} for i in ids[1:]]
    worker_suggestions.record(rows)
    got = worker_suggestions.latest()["suggestions"]
    assert [s["template_id"] for s in got] == ids[:8]
    assert got[0]["reason"] == "a"


def test_an_empty_or_unknown_answer_records_nothing_and_keeps_the_earlier_one():
    worker_suggestions.record([{"template_id": some_ids(1)[0], "reason": "first"}])
    worker_suggestions.record([])
    worker_suggestions.record([{"template_id": "nope", "reason": "x"}])
    assert [s["reason"] for s in worker_suggestions.latest()["suggestions"]] == ["first"]


def test_a_newer_answer_replaces_the_older_one():
    a, b = some_ids(2)
    worker_suggestions.record([{"template_id": a, "reason": "old"}])
    worker_suggestions.record([{"template_id": b, "reason": "new"}])
    assert [s["template_id"] for s in worker_suggestions.latest()["suggestions"]] == [b]


def test_an_old_suggestion_is_not_offered():
    worker_suggestions.record([{"template_id": some_ids(1)[0], "reason": "r"}])
    created = worker_suggestions.latest()["created_at"]
    assert worker_suggestions.latest(now=created + worker_suggestions.FRESH_SECONDS + 1) is None


def test_ids_that_left_the_catalog_are_dropped_on_read(tmp_path):
    keep = some_ids(1)[0]
    path = tmp_path / "state" / "worker-suggestions.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"created_at": 9e12, "suggestions": [{"template_id": "gone", "reason": "x"}, {"template_id": keep, "reason": "y"}]}))
    assert [s["template_id"] for s in worker_suggestions.latest(now=9e12)["suggestions"]] == [keep]


def test_a_damaged_file_reads_as_nothing(tmp_path):
    path = tmp_path / "state" / "worker-suggestions.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert worker_suggestions.latest() is None


async def test_the_lead_tool_remembers_its_answer_and_creates_nothing():
    run = SimpleNamespace(thread_id=7)
    result = await setup.call(None, run, "suggest_workers", {"goals": ["briefing"]})
    assert result["suggestions"][0]["template_id"] == "operations-chief-of-staff"
    stored = worker_suggestions.latest()
    assert [s["template_id"] for s in stored["suggestions"]] == [s["template_id"] for s in result["suggestions"]]


async def test_a_bad_tool_call_remembers_nothing():
    with pytest.raises(Exception):
        await setup.call(None, SimpleNamespace(thread_id=7), "suggest_workers", {"goals": "leads"})
    assert worker_suggestions.latest() is None


async def test_endpoint_returns_null_then_the_latest_known_ids(api):
    assert (await api.get(LATEST)).json() is None
    worker_suggestions.record([{"template_id": some_ids(1)[0], "reason": "fits"}])
    body = (await api.get(LATEST)).json()
    assert [s["template_id"] for s in body["suggestions"]] == some_ids(1)
    assert body["suggestions"][0]["reason"] == "fits"
    assert isinstance(body["created_at"], float)
