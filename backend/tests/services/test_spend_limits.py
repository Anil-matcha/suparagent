"""Spending limits for paid marketplace tools: the three limits, settling, scheduled paid use and who may change them."""
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.session import get_db
from app.db.models import Agent, Approval, Event, CommunityMcpConnection, CommunityMcpToolGrant, SpendLedger, Thread, Turn
from app.db.models.routine import Routine, RoutineAppAction, RoutineRun
from app.routers.agents import get_runtime as agents_runtime
from app.routers.spend import router as spend_router
from app.services import routine_uninterrupted, spend, thread_store
from app.services.community_mcp_bridge import CommunityMcpBridge
from tests.services.thread_harness import owner, sessions  # noqa: F401

PAID_SCHEMA = {"type": "object", "properties": {"max_cost_usd": {"type": "number"}, "q": {"type": "string"}},
               "required": ["max_cost_usd"], "additionalProperties": False}


@pytest.fixture
async def world(sessions, owner, monkeypatch):
    monkeypatch.setattr(settings, "HOST_EXECUTION_DISABLED", False)
    monkeypatch.setattr(settings, "SPEND_PER_CALL_CENTS", 50)
    monkeypatch.setattr(settings, "SPEND_PER_RUN_CENTS", 200)
    monkeypatch.setattr(settings, "SPEND_PER_WORKER_MONTH_CENTS", 2000)
    async with sessions() as db:
        agent = Agent(owner_id=owner, name="Paid")
        db.add(agent)
        await db.flush()
        thread = Thread(owner_id=owner, agent_id=agent.id, provider="codex")
        db.add(thread)
        await db.flush()
        turn = Turn(thread_id=thread.id, seq=1, prompt_text="Research", origin="routine", status="running", owner_token="owner-token")
        routine = Routine(owner_id=owner, agent_id=agent.id, name="R", prompt="P", schedule_kind="manual",
                          schedule_spec={}, uninterrupted_access=True)
        connection = CommunityMcpConnection(id=uuid4().hex, owner_id=owner, slug="test-" + uuid4().hex, section="community",
            manifest={"auth_mode": "none", "name": "Data", "slug": "data", "endpoint": "https://example.com/mcp"},
            manifest_digest="manifest-1", revision=1)
        db.add_all([turn, routine, connection])
        await db.flush()
        db.add(CommunityMcpToolGrant(agent_id=agent.id, connection_id=connection.id, tool_name="data_call", schema_digest="schema-1",
                                     description="Paid lookup", input_schema=PAID_SCHEMA))
        await db.flush()
        ceiling = await routine_uninterrupted.snapshot(db, owner, agent.id)
        occurrence = RoutineRun(routine_id=routine.id, trigger="schedule", state="running", thread_id=thread.id, turn_id=turn.id,
                                uninterrupted_access=True, capability_snapshot=ceiling)
        db.add(occurrence)
        await db.commit()
    runtime = NS(owner_id=owner, session=sessions)
    run = NS(agent_id=agent.id, routine_run_id=occurrence.id, thread_id=thread.id, turn_id=turn.id, owner_token="owner-token",
             uninterrupted_access=True, ctx=NS(computer=NS(computer_id=7), browser=None, team=NS(), draft_only=False,
                                              media_review_only=False, aborted=False, community_tools=[], approval_mode="ask"))
    adapter = NS(key="codex", capabilities=NS(mcp=True, approvals=True))
    mounted = await CommunityMcpBridge.for_turn(runtime, run, adapter)
    bridge = CommunityMcpBridge(runtime, run, mounted)
    [alias] = bridge.aliases_allowed
    run.ctx.community_tools = [alias]
    yield NS(runtime=runtime, run=run, bridge=bridge, alias=alias, agent=agent, routine=routine, occurrence=occurrence,
             connection=connection, sessions=sessions, owner=owner)
    await bridge.close()
    async with sessions() as db:
        await db.execute(delete(SpendLedger).where(SpendLedger.owner_id == owner))
        await db.execute(delete(CommunityMcpToolGrant).where(CommunityMcpToolGrant.agent_id == agent.id))
        await db.execute(delete(CommunityMcpConnection).where(CommunityMcpConnection.id == connection.id))
        await db.execute(delete(RoutineAppAction).where(RoutineAppAction.run_id == occurrence.id))
        await db.delete(await db.get(RoutineRun, occurrence.id))
        await db.delete(await db.get(Routine, routine.id))
        await db.execute(delete(Approval).where(Approval.thread_id == thread.id))
        await db.execute(delete(Event).where(Event.thread_id == thread.id))
        await db.execute(delete(Turn).where(Turn.thread_id == thread.id))
        await db.execute(delete(Thread).where(Thread.id == thread.id))
        await db.delete(await db.get(Agent, agent.id))
        await db.commit()


class Remote:
    def __init__(self, result=None):
        self.calls, self.result = [], result or {"content": [{"type": "text", "text": "done"}], "isError": False}

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


async def paid_call(w, remote, **arguments):
    async with w.sessions() as db:
        row = await thread_store.create_approval(db, thread_id=w.run.thread_id, turn_id=w.run.turn_id,
            request_id=uuid4().hex, tool="mcp__community__" + w.alias, input=arguments, summary="Paid lookup", kind="other",
            owner_token=w.run.owner_token)
        await thread_store.decide_approval(db, row.id, "allowed", by={"by": "routine_uninterrupted_grant"})
    return await w.bridge._consume_and_call(remote, w.alias, arguments)


async def ledger(w):
    async with w.sessions() as db:
        return list((await db.scalars(select(SpendLedger).where(SpendLedger.owner_id == w.owner).order_by(SpendLedger.id))).all())


def test_cents_round_up_and_reject_bad_amounts():
    assert spend.to_cents(0.5) == 50 and spend.to_cents("0.501") == 51 and spend.to_cents(2) == 200
    assert spend.to_cents(-1) is None and spend.to_cents(None) is None and spend.to_cents(True) is None
    assert spend.to_cents("abc") is None and spend.to_cents(float("nan")) is None
    assert spend.is_guarded(PAID_SCHEMA) and not spend.is_guarded({"type": "object", "properties": {}})


async def test_a_call_over_the_per_call_limit_is_refused_and_the_approval_is_kept(world):
    remote = Remote()
    result = await paid_call(world, remote, max_cost_usd=0.6)
    assert result["code"] == "spend_limit" and result["reason"] == "per_call"
    assert remote.calls == []
    [row] = await ledger(world)
    assert (row.status, row.reason) == ("refused", "per_call")
    # The refusal did not use up the approval, so a call inside the limit can still be made with it.
    ok = await world.bridge._consume_and_call(remote, world.alias, {"max_cost_usd": 0.6})
    assert ok["code"] == "spend_limit"


async def test_a_call_without_a_cost_cap_is_refused(world):
    remote = Remote()
    result = await paid_call(world, remote, max_cost_usd=0, q="x")
    assert result["code"] == "spend_limit" and result["reason"] == "no_cap" and remote.calls == []


async def test_a_run_stops_at_its_limit(world, monkeypatch):
    monkeypatch.setattr(settings, "SPEND_PER_RUN_CENTS", 100)
    remote = Remote()
    assert "code" not in await paid_call(world, remote, max_cost_usd=0.5, q="a")
    assert "code" not in await paid_call(world, remote, max_cost_usd=0.5, q="b")
    third = await paid_call(world, remote, max_cost_usd=0.5, q="c")
    assert third["code"] == "spend_limit" and third["reason"] == "per_run"
    assert len(remote.calls) == 2
    async with world.sessions() as db:
        summary = await spend.run_summary(db, f"routine:{world.occurrence.id}")
    assert summary == {"cents": 100, "calls": 2, "refused": ["per_run"]}


async def test_a_worker_stops_at_its_monthly_limit(world, monkeypatch):
    monkeypatch.setattr(settings, "SPEND_PER_WORKER_MONTH_CENTS", 60)
    remote = Remote()
    assert "code" not in await paid_call(world, remote, max_cost_usd=0.5, q="a")
    second = await paid_call(world, remote, max_cost_usd=0.5, q="b")
    assert second["code"] == "spend_limit" and second["reason"] == "monthly"
    async with world.sessions() as db:
        assert await spend.month_spent(db, world.owner, world.agent.id) == 50


async def test_a_call_settles_at_the_reported_cost_or_at_its_cap(world):
    priced = Remote({"content": [], "isError": False, "structuredContent": {"estimated_cost_usd": 0.12}})
    await paid_call(world, priced, max_cost_usd=0.5, q="a")
    unpriced = Remote()
    await paid_call(world, unpriced, max_cost_usd=0.4, q="b")
    texted = Remote({"content": [{"type": "text", "text": '{"result": 1, "estimated_cost_usd": 0.07}'}], "isError": False})
    await paid_call(world, texted, max_cost_usd=0.3, q="c")
    rows = await ledger(world)
    assert [(r.status, r.cap_cents, r.settled_cents) for r in rows] == [("settled", 50, 12), ("settled", 40, 40), ("settled", 30, 7)]


async def test_a_reserved_call_that_never_went_out_is_released(world):
    async with world.sessions() as db:
        row, refusal = await spend.reserve(db, owner_id=world.owner, agent_id=world.agent.id, run_key="turn:1",
                                           routine_run_id=None, tool="t", cap_cents=10)
        assert refusal is None and await spend.run_spent(db, "turn:1") == 10
        await spend.release(db, row.id)
        await db.commit()
        assert (await db.get(SpendLedger, row.id)).status == "released"
        assert await spend.run_spent(db, "turn:1") == 0


async def test_scheduled_paid_use_is_off_until_the_owner_turns_it_on(world):
    args = {"max_cost_usd": 0.1}
    tool = "mcp__community__" + world.alias
    assert await routine_uninterrupted.automatic(world.runtime, world.run, tool, args) is None
    async with world.sessions() as db:
        (await db.get(Agent, world.agent.id)).scheduled_paid_use = True
        await db.commit()
    assert await routine_uninterrupted.automatic(world.runtime, world.run, tool, args) == ("allowed", "routine_uninterrupted_grant")


async def test_a_tool_without_a_cost_cap_is_not_held_by_the_paid_switch(world):
    async with world.sessions() as db:
        grant = await db.get(CommunityMcpToolGrant, (world.agent.id, world.connection.id, "data_call"))
        grant.input_schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        await db.commit()
    result = await routine_uninterrupted.automatic(world.runtime, world.run, "mcp__community__" + world.alias, {"q": "x"})
    assert result == ("allowed", "routine_uninterrupted_grant")


async def test_a_run_with_a_refusal_ends_partial_and_a_monthly_refusal_pauses_the_routine(world, monkeypatch):
    from datetime import UTC, datetime
    from app.services.routine_service import scheduler
    monkeypatch.setattr(settings, "SPEND_PER_WORKER_MONTH_CENTS", 40)
    await paid_call(world, Remote(), max_cost_usd=0.5, q="a")
    async with world.sessions() as db:
        routine, run = await db.get(Routine, world.routine.id), await db.get(RoutineRun, world.occurrence.id)
        turn = await db.get(Turn, world.run.turn_id)
        turn.status, turn.result = "succeeded", "Report"
        await scheduler._settle(db, routine, run, turn, datetime.now(UTC))
        await db.commit()
        assert run.outcome == "partial" and run.state == "completed"
        assert run.outcome_details["spend"]["refused"] == ["monthly"]
        assert (routine.paused, routine.paused_reason) == (True, "spend_limit")


async def test_the_spend_line_is_kept_when_nothing_was_refused(world):
    from datetime import UTC, datetime
    from app.services.routine_service import scheduler
    await paid_call(world, Remote({"content": [], "isError": False, "structuredContent": {"estimated_cost_usd": 0.2}}),
                    max_cost_usd=0.5, q="a")
    async with world.sessions() as db:
        routine, run = await db.get(Routine, world.routine.id), await db.get(RoutineRun, world.occurrence.id)
        turn = await db.get(Turn, world.run.turn_id)
        turn.status, turn.result = "succeeded", "Report"
        await scheduler._settle(db, routine, run, turn, datetime.now(UTC))
        await db.commit()
        assert run.outcome == "report" and run.outcome_details["spend"] == {"cents": 20, "calls": 1, "refused": []}
        assert routine.paused is False


@pytest.fixture
async def api(world):
    app = FastAPI()
    app.include_router(spend_router, prefix="/api/v1")
    app.dependency_overrides[agents_runtime] = lambda: world.runtime

    async def db_of_this_test():
        # The router's own get_db uses the app's global engine, whose pooled PostgreSQL connection can belong to
        # another test's event loop ("another operation is in progress"). Use this test's own sessions instead.
        async with world.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_of_this_test
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=("127.0.0.1", 50000)), base_url="http://localhost") as client:
        yield client


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


async def test_the_owner_reads_limits_and_turns_scheduled_paid_use_on_and_off(api, world):
    body = (await api.get("/api/v1/spend-limits")).json()
    assert {k: v for k, v in body["limits"].items() if k != "sources"} == {"per_call_cents": 50, "per_run_cents": 200, "per_worker_month_cents": 2000}
    mine = next(w for w in body["workers"] if w["agent_id"] == world.agent.id)
    assert mine["scheduled_paid_use"] is False
    on = await api.put(f"/api/v1/spend-limits/workers/{world.agent.id}", json={"scheduled_paid_use": True})
    assert on.status_code == 200 and on.json()["scheduled_paid_use"] is True
    off = await api.put(f"/api/v1/spend-limits/workers/{world.agent.id}", json={"scheduled_paid_use": False})
    assert off.json()["scheduled_paid_use"] is False
    assert (await api.put("/api/v1/spend-limits/workers/999999999", json={"scheduled_paid_use": True})).status_code == 404
    assert (await api.put(f"/api/v1/spend-limits/workers/{world.agent.id}", json={"scheduled_paid_use": "yes"})).status_code == 422


@pytest.fixture
async def clean_limit_rows(world):
    from app.db.models.app_setting import AppSetting, SettingAudit
    yield
    async with world.sessions() as db:
        keys = list(spend.LIMIT_KEYS.values())
        await db.execute(delete(AppSetting).where(AppSetting.key.in_(keys)))
        await db.execute(delete(SettingAudit).where(SettingAudit.key.in_(keys)))
        await db.commit()


async def test_a_headless_owner_can_change_the_limits_and_sees_where_each_comes_from(api, clean_limit_rows):
    before = (await api.get("/api/v1/spend-limits")).json()["limits"]
    assert set(before["sources"]) == {"per_call", "per_run", "monthly"} and set(before["sources"].values()) <= {"environment", "default"}
    response = await api.put("/api/v1/spend-limits", json={"per_call_cents": 10, "per_run_cents": 20, "per_worker_month_cents": 30})
    assert response.status_code == 200
    assert (response.json()["per_call_cents"], response.json()["per_run_cents"], response.json()["per_worker_month_cents"]) == (10, 20, 30)
    after = (await api.get("/api/v1/spend-limits")).json()["limits"]
    assert after["sources"] == {"per_call": "database", "per_run": "database", "monthly": "database"}
    assert (await api.put("/api/v1/spend-limits", json={"per_call_cents": -1, "per_run_cents": 20, "per_worker_month_cents": 30})).status_code == 422


async def test_a_saved_headless_limit_is_what_a_paid_call_is_held_to(world, clean_limit_rows):
    async with world.sessions() as db:
        await spend.save_headless_limits(db, {"per_call": 10, "per_run": 200, "monthly": 2000})
    result = await paid_call(world, Remote(), max_cost_usd=0.5, q="a")
    assert result["code"] == "spend_limit" and result["reason"] == "per_call"


async def test_a_headless_limit_outside_its_range_is_refused(world, clean_limit_rows):
    async with world.sessions() as db:
        with pytest.raises(ValueError):
            await spend.save_headless_limits(db, {"per_call": 100_001, "per_run": 200, "monthly": 2000})
        with pytest.raises(ValueError):
            await spend.save_headless_limits(db, {"per_call": True, "per_run": 200, "monthly": 2000})


def test_no_agent_facing_surface_can_reach_the_limits_or_the_switch():
    import app.schemas.agent as agent_schemas
    root = Path(spend.__file__).parents[1]
    for name in ("AgentCreateRequest", "AgentUpdateRequest"):
        fields = getattr(agent_schemas, name).model_fields
        assert "scheduled_paid_use" not in fields
    forbidden = ("SPEND_PER", "scheduled_paid_use", "services.spend", "import spend")
    for folder in ("team", "templates"):
        for path in (root / folder).rglob("*.py"):
            text = path.read_text()
            assert not any(word in text for word in forbidden), (path.name, "must not touch spending limits")


async def reserve_once(w, run_key, cap_cents):
    async with w.sessions() as db:
        row, reason = await spend.reserve(db, owner_id=w.owner, agent_id=w.agent.id, run_key=run_key,
                                          routine_run_id=None, tool="t", cap_cents=cap_cents)
        await asyncio.sleep(0.05)  # hold the transaction open so the other call has to wait for it
        await db.commit()
        return reason


async def test_two_calls_at_once_cannot_both_pass_the_run_limit(world, monkeypatch):
    monkeypatch.setattr(settings, "SPEND_PER_CALL_CENTS", 150)
    monkeypatch.setattr(settings, "SPEND_PER_RUN_CENTS", 200)
    reasons = await asyncio.gather(reserve_once(world, "turn:race", 150), reserve_once(world, "turn:race", 150))
    assert sorted(r or "ok" for r in reasons) == ["ok", "per_run"]
    async with world.sessions() as db:
        assert await spend.run_spent(db, "turn:race") == 150


async def test_two_calls_at_once_cannot_both_pass_the_monthly_limit(world, monkeypatch):
    monkeypatch.setattr(settings, "SPEND_PER_CALL_CENTS", 200)
    monkeypatch.setattr(settings, "SPEND_PER_RUN_CENTS", 1000)
    monkeypatch.setattr(settings, "SPEND_PER_WORKER_MONTH_CENTS", 300)
    reasons = await asyncio.gather(reserve_once(world, "turn:m1", 200), reserve_once(world, "turn:m2", 200))
    assert sorted(r or "ok" for r in reasons) == ["monthly", "ok"]
    async with world.sessions() as db:
        assert await spend.month_spent(db, world.owner, world.agent.id) == 200


class Failing:
    def __init__(self, error):
        self.error, self.calls = error, []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        raise self.error


async def test_a_call_that_clearly_never_ran_costs_nothing(world):
    from app.community_mcp import McpError
    for error in (McpError("authentication_failed", "x", 502, not_run=True),
                  McpError("remote_error", "x", 502, not_run=True),
                  McpError("connect_failed", "x", 502)):
        result = await paid_call(world, Failing(error), max_cost_usd=0.5, q=error.code)
        # A known refusal keeps its own code; a failed connection is not marked not_run, so it stays unknown.
        assert result["code"] == (error.code if error.not_run else "outcome_unknown")
    rows = await ledger(world)
    assert [(r.status, r.settled_cents) for r in rows] == [("released", 0)] * 3
    assert [r.reason for r in rows] == ["not_run:authentication_failed", "not_run:remote_error", "not_run:connect_failed"]
    async with world.sessions() as db:
        assert await spend.run_spent(db, f"routine:{world.occurrence.id}") == 0


async def test_an_unconfirmed_call_keeps_its_cap_and_says_why(world):
    from app.community_mcp import McpError
    for error in (McpError("timeout", "x", 504), McpError("remote_unavailable", "x", 502),
                  McpError("remote_error", "x", 502, not_run=False)):
        await paid_call(world, Failing(error), max_cost_usd=0.2, q=error.code)
    rows = await ledger(world)
    assert [(r.status, r.settled_cents) for r in rows] == [("settled", 20)] * 3
    assert [r.reason for r in rows] == ["unconfirmed:timeout", "unconfirmed:remote_unavailable", "unconfirmed:remote_error"]


async def test_a_tool_error_with_no_cost_keeps_the_cap_and_a_priced_call_has_no_reason(world):
    await paid_call(world, Remote({"content": [{"type": "text", "text": "bad"}], "isError": True}), max_cost_usd=0.3, q="a")
    await paid_call(world, Remote({"content": [], "isError": False, "structuredContent": {"estimated_cost_usd": 0.1}}),
                    max_cost_usd=0.3, q="b")
    rows = await ledger(world)
    assert [(r.settled_cents, r.reason) for r in rows] == [(30, "tool_error"), (10, None)]


async def test_a_limit_check_counts_only_this_calendar_month_in_utc(world):
    from datetime import UTC, datetime
    async with world.sessions() as db:
        for stamp, cents in ((datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC), 40), (datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC), 25)):
            db.add(SpendLedger(owner_id=world.owner, agent_id=world.agent.id, run_key="turn:old", tool="t", status="settled",
                               cap_cents=cents, settled_cents=cents, created_at=stamp))
        await db.commit()
        assert await spend.month_spent(db, world.owner, world.agent.id, datetime(2026, 11, 17, tzinfo=UTC)) == 25
        assert await spend.month_spent(db, world.owner, world.agent.id, datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC)) == 40
        assert await spend.month_spent(db, world.owner, world.agent.id, datetime(2026, 12, 1, tzinfo=UTC)) == 0
    assert spend.month_start(datetime(2027, 1, 15, 8, tzinfo=UTC)) == datetime(2027, 1, 1, tzinfo=UTC)
    assert spend.month_end(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == datetime(2027, 1, 1, tzinfo=UTC)
    assert spend.month_end(datetime(2026, 11, 17, tzinfo=UTC)) == datetime(2026, 12, 1, tzinfo=UTC)


def test_cents_are_exact_at_the_edges():
    assert spend.to_cents(0.1 + 0.2) == 30          # float noise does not cost an extra cent
    assert spend.to_cents("0.001") == 1 and spend.to_cents("0.01") == 1 and spend.to_cents("0.010001") == 2
    assert spend.to_cents(0) == 0 and spend.to_cents("0.0000004") == 0
    assert spend.to_cents(1e-9) == 0 and spend.to_cents("1e2") == 10000
    assert spend.to_cents(0.29) == 29 and spend.to_cents(19.99) == 1999
