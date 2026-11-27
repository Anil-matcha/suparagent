"""Inline pages: the two tools (show_page, update_page) through a real stdio server and
the bridge, the events they record, rooms, tool policy, launch args and engine mounting."""

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.models import Event
from app.services import agent_store, pages_bridge, thread_store, tool_policy
from app.services.provider_adapter import TurnOutcome
from tests.services.test_acp_adapter import FAKE, FakeAcp, acp, acp_thread, art  # noqa: F401
from tests.services.test_codex_adapter import FakeCodex, codex, crt  # noqa: F401
from tests.services.test_memory_log_brief import harness, make_agent  # noqa: F401
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401


def test_policy_allows_the_two_page_tools_only_and_not_in_a_draft_only_run():
    run = SimpleNamespace(ctx=SimpleNamespace(team=None))
    assert tool_policy.automatic("mcp__pages__show_page", "ask", run, {}) == ("allowed", "pages_show")
    assert tool_policy.automatic("mcp__pages__update_page", "plan", run, {}) == ("allowed", "pages_show")
    assert tool_policy.automatic("mcp__pages__delete", "ask", run, {})[0] == "denied"
    draft = SimpleNamespace(ctx=SimpleNamespace(team=None, draft_only=True))
    assert tool_policy.automatic("mcp__pages__show_page", "ask", draft, {}) == ("denied", "draft_only_run")


def test_a_pages_call_is_recognised_only_for_the_tools_of_the_pages_server():
    ok = pages_bridge.is_pages_call
    assert ok(server="pages", tool="show_page") and ok(server="pages", tool="update_page")
    assert not ok(server="pages", tool="other") and not ok(server="memory", tool="show_page")
    for title in ("show_page (pages MCP Server)", "mcp__pages__update_page", "pages/show_page"):
        assert ok(title=title, raw={"html": "<p>x</p>", "title": "t"}), title
    assert not ok(title="show_page", raw={"title": "x"})
    assert not ok(title="show_page (pages)", raw={"command": "rm"})


@pytest.mark.parametrize("args, fragment", [
    ({"html": "<p>x</p>"}, "title is required"),
    ({"html": "<p>x</p>", "title": "x" * 81}, "too long"),
    ({"html": "", "title": "t"}, "html must be"),
    ({"html": "a" * 262145, "title": "t"}, "too large"),
    ({"html": "<p>x</p>", "title": "t", "height": 79}, "height"),
    ({"html": "<p>x</p>", "title": "t", "height": 721}, "height"),
    ({"html": "<p>x</p>", "title": "t", "height": True}, "height"),
    ({"html": "<p>x</p>", "title": "t", "frameless": "yes"}, "frameless"),
    ({"html": "<p>x</p>", "title": "t", "uses": "state"}, "uses"),
    ({"html": "<p>x</p>", "title": "t", "uses": [1]}, "uses"),
    ({"html": "<p>x</p>", "title": "t", "uses": ["s" * 201]}, "uses"),
    ({"html": "<p>x</p>", "title": "t", "uses": ["a"] * 25}, "uses"),
])
def test_invalid_input_is_refused_in_words_the_model_can_fix(args, fragment):
    with pytest.raises(pages_bridge.PageError, match=fragment):
        pages_bridge._check_common(args, creating=True)


def test_valid_input_and_exact_size_limit():
    out = pages_bridge._check_common({"html": "a" * 262144, "title": "T", "height": 80, "uses": ["state", "gmail:GMAIL_GET_PROFILE"]}, creating=True)
    assert out["height"] == 80 and out["uses"] == ["state", "gmail:GMAIL_GET_PROFILE"]


def _client(launch):
    proc = subprocess.Popen([launch.command, *launch.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            env={"PATH": "/usr/bin:/bin", **launch.env}, text=True)

    async def rpc(method, params=None, id=1):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}) + "\n")
        proc.stdin.flush()
        line = await asyncio.get_running_loop().run_in_executor(None, proc.stdout.readline)
        return json.loads(line)["result"]

    return proc, rpc


async def test_show_update_events_rev_and_replay_end_to_end(harness, sessions, owner):
    rt, engine = harness
    agent = await make_agent(sessions, owner, name="Lead")
    seen = {}

    async def run_turn(ctx, sink, gate):
        proc, rpc = _client(ctx.pages_launch)
        try:
            await rpc("initialize")
            seen["tools"] = {t["name"] for t in (await rpc("tools/list"))["tools"]}
            shown = await rpc("tools/call", {"name": "show_page", "arguments": {"html": "<p>hi</p>", "title": "Hello", "uses": ["state"]}})
            seen["shown"] = shown
            page_id = shown["content"][0]["text"].split("page_id ")[1].split(",")[0]
            seen["updated"] = await rpc("tools/call", {"name": "update_page", "arguments": {"page_id": page_id, "html": "<p>v2</p>", "height": 500}})
            seen["unknown"] = await rpc("tools/call", {"name": "update_page", "arguments": {"page_id": "nope", "html": "<p>x</p>"}})
            seen["bad"] = await rpc("tools/call", {"name": "show_page", "arguments": {"html": "<p>x</p>"}})
            seen["page_id"] = page_id
        finally:
            proc.kill()
        await sink.emit("assistant.message", {"text": "ok"})
        return TurnOutcome("succeeded", result="ok")

    engine.run_turn = run_turn
    thread = await rt.create_thread(provider="claude", agent_id=agent.id)
    await rt.send_turn(thread.id, "show me")
    await finished(sessions, thread.id)
    assert seen["tools"] == {"show_page", "update_page"}
    assert not seen["shown"]["isError"] and not seen["updated"]["isError"]
    assert seen["unknown"]["isError"] and "show_page" in seen["unknown"]["content"][0]["text"]
    assert seen["bad"]["isError"] and "title is required" in seen["bad"]["content"][0]["text"]
    events = [e for e in await events_of(sessions, thread.id) if e.type == "page.shown"]
    assert [e.data["rev"] for e in events] == [1, 2]
    first, second = events[0].data, events[1].data
    assert first["page_id"] == second["page_id"] == seen["page_id"]
    assert first == {"page_id": seen["page_id"], "rev": 1, "title": "Hello", "html": "<p>hi</p>", "height": 360,
                     "frameless": False, "uses": ["state"]}
    # an update keeps what it leaves out (title, uses) and changes what it names
    assert second["title"] == "Hello" and second["uses"] == ["state"] and second["html"] == "<p>v2</p>" and second["height"] == 500
    # replay ignores page events
    items = thread_store.history_items(await events_of(sessions, thread.id))
    assert all(i["role"] in ("user", "assistant", "tool") for i in items)
    assert not any("<p>" in json.dumps(i) for i in items)


async def test_an_earlier_turns_page_can_be_updated_and_a_room_is_refused(harness, sessions, owner):
    rt, engine = harness
    agent = await make_agent(sessions, owner, name="Lead")
    seen = {"calls": []}

    async def run_turn(ctx, sink, gate):
        proc, rpc = _client(ctx.pages_launch)
        try:
            await rpc("initialize")
            if seen.get("page_id"):
                args = {"page_id": seen["page_id"], "html": "<p>later</p>"}
                seen["calls"].append(await rpc("tools/call", {"name": "update_page", "arguments": args}))
            else:
                r = await rpc("tools/call", {"name": "show_page", "arguments": {"html": "<p>a</p>", "title": "A"}})
                seen["page_id"] = r["content"][0]["text"].split("page_id ")[1].split(",")[0]
        finally:
            proc.kill()
        await sink.emit("assistant.message", {"text": "ok"})
        return TurnOutcome("succeeded", result="ok")

    engine.run_turn = run_turn
    thread = await rt.create_thread(provider="claude", agent_id=agent.id)
    await rt.send_turn(thread.id, "one")
    await finished(sessions, thread.id)
    await rt.send_turn(thread.id, "two")
    await finished(sessions, thread.id, count=2)
    revs = [e.data["rev"] for e in await events_of(sessions, thread.id) if e.type == "page.shown"]
    assert revs == [1, 2]


async def test_pages_are_not_mounted_in_a_room_thread(harness, sessions, owner):
    rt, engine = harness
    agent = await make_agent(sessions, owner, name="Lead")
    launches = []

    async def run_turn(ctx, sink, gate):
        launches.append(ctx.pages_launch)
        await sink.emit("assistant.message", {"text": "ok"})
        return TurnOutcome("succeeded", result="ok")

    engine.run_turn = run_turn
    room = await rt.rooms.create({"name": "Standup", "member_ids": [agent.id], "default_responder_kind": "everyone"})
    await rt.rooms.post(room["id"], "hello")
    await rt.rooms.work(room["id"])
    assert launches and all(launch is None for launch in launches)


async def test_the_bridge_itself_refuses_a_room_thread(harness, sessions, owner):
    rt, _ = harness
    agent = await make_agent(sessions, owner, name="Lead")
    room = await rt.rooms.create({"name": "Standup", "member_ids": [agent.id], "default_responder_kind": "everyone"})
    from app.db.models import Thread
    from sqlalchemy import select
    async with sessions() as db:
        member = (await db.execute(select(Thread).where(Thread.room_id == room["id"]))).scalars().first()
    run = SimpleNamespace(turn_id=1, thread_id=member.id, ctx=SimpleNamespace(aborted=False), recorder=None)
    bridge = pages_bridge.PagesBridge(rt, run)
    try:
        result = await bridge.execute("show_page", {"html": "<p>x</p>", "title": "t"})
    finally:
        await bridge.close()
    assert result["error"].startswith("Pages cannot be shown in a room") and "text" in result["error"]


def test_launch_args_and_frozen_flag(monkeypatch):
    run = SimpleNamespace(turn_id=7, ctx=SimpleNamespace(aborted=False))
    bridge = pages_bridge.PagesBridge(None, run)
    try:
        launch = bridge.launch
        assert launch.args[:2] == ["-m", "app.pages_mcp_server"] and launch.args[-2] == "--mailbox"
        assert "suparagent-pages-7-" in launch.args[-1] and launch.env["PYTHONPATH"]
        monkeypatch.setattr("sys.frozen", True, raising=False)
        assert bridge.launch.args[0] == "--pages-mcp"
    finally:
        import shutil
        shutil.rmtree(bridge.folder, ignore_errors=True)
    assert "--pages-mcp" in (Path(__file__).parents[2] / "desktop_main.py").read_text()


async def test_codex_mounts_the_pages_server_with_its_tools(crt, sessions, owner, codex):
    agent = await agent_store_create(sessions, owner, "codex")
    thread = await crt.create_thread(provider="codex", agent_id=agent.id)
    await crt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    params = codex.received("thread/start")[0]["params"]
    server = params["config"]["mcp_servers"]["pages"]
    assert server["enabled_tools"] == ["show_page", "update_page"] and server["default_tools_approval_mode"] == "approve"
    assert server["args"][-2] == "--mailbox" and "suparagent-pages-" in server["args"][-1]
    assert pages_bridge.TOOL_NOTE in params["developerInstructions"]


async def test_acp_mounts_the_pages_server_beside_memory(acp, sessions, owner, art):
    agent = await agent_store_create(sessions, owner, "acp")
    thread = await art.create_thread(provider="acp", agent_id=agent.id)
    await art.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    servers = acp.received("session/new")[0]["params"]["mcpServers"]
    assert [s["name"] for s in servers] == ["memory", "pages"]
    assert pages_bridge.TOOL_NOTE in acp.prompts()[0]


async def agent_store_create(sessions, owner, provider):
    async with sessions() as db:
        return await agent_store.create_agent(db, owner_id=owner, name="Scout", provider=provider)
