"""A share of the mailbox (BL-172): enforced on every Gmail call, through the real bridge, gate and run_tool."""
import json

import httpx
import pytest
from sqlalchemy import select

from app import composio_tools, gmail_share
from app.db.models import Agent
from app.services import agent_store, composio_bridge, composio_service
from app.services.composio_client import ComposioClient
from app.services.provider_adapter import TurnOutcome
from tests.services.test_composio_bridge import FakeComposio, KEY, ask, one_turn, world  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions, wait_until  # noqa: F401

SEED = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
SECRET = "THE_BODY_OF_A_HIDDEN_MESSAGE"
ACCOUNT = "ca_g"


def entry(percent, pins=()):
    return {"percent": percent, "seed": SEED, "pins": list(pins)}


def rule(percent, pins=()):
    return gmail_share.rule_from_json(entry(percent, pins))


class GmailFake(FakeComposio):
    """Composio with a pageable Gmail: 30 rows a page, 6 pages, thread ids th0..th179."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        route = f"{request.method} {request.url.path.removeprefix('/api/v3.1')}"
        if route == "GET /connected_accounts":
            slug = request.url.params.get("toolkit_slugs")
            items = [a for a in self.accounts if slug is None or a["toolkit"]["slug"] == slug]
            return httpx.Response(200, json={"items": items})
        if route.endswith("/execute"):
            body = json.loads(request.content)
            slug, args = body["tool_slug"], body["arguments"]
            self.executes.append({"slug": slug, "arguments": args, "account": body.get("account")})
            data = self.gmail(slug, args)
            if data is not None:
                return httpx.Response(200, json={"result_type": "completed", "data": data, "error": None})
        return super().handler(request)

    def gmail(self, slug, args):
        if slug == "GMAIL_FETCH_EMAILS":
            page = int(args.get("page_token", "p0")[1:])
            rows = [{"messageId": f"m_th{page * 30 + i}", "threadId": f"th{page * 30 + i}", "subject": "s", "sender": "a@b.c",
                     "preview": {"body": "hi"}} for i in range(30)]
            data = {"messages": rows}
            if page < 5:
                data["nextPageToken"] = f"p{page + 1}"
            return data
        if slug == "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID":
            return {"messageId": args["message_id"], "threadId": args["message_id"].removeprefix("m_"), "subject": "s",
                    "sender": "a@b.c", "messageText": SECRET}
        if slug == "GMAIL_FETCH_MESSAGE_BY_THREAD_ID":
            return {"messages": [{"messageId": "m1", "threadId": args["thread_id"], "messageText": "thread text"}]}
        if slug == "GMAIL_GET_PROFILE":
            return {"emailAddress": "owner@example.com", "messagesTotal": 98765, "threadsTotal": 43210, "historyId": "7"}
        if slug in ("GMAIL_CREATE_EMAIL_DRAFT", "GMAIL_SEND_EMAIL"):
            return {"id": "d1", "message": {"id": "m9", "threadId": args.get("thread_id", "t")}}
        return None


@pytest.fixture
async def fake(monkeypatch):
    fake = GmailFake()
    monkeypatch.setattr(composio_bridge, "make_client", lambda key: ComposioClient(key, transport=httpx.MockTransport(fake.handler)))
    return fake


def thread_in(r, wanted=True, start=0):
    return next(f"th{n}" for n in range(start, start + 100_000) if gmail_share.in_share(r, f"th{n}") is wanted)


async def grant(sessions, agent, shares, accounts=(ACCOUNT,), level="read"):
    async with sessions() as db:
        await agent_store.update_agent(db, agent.id, connector_scopes={"gmail": level}, connector_account_scopes={"gmail": list(accounts)})
        row = await db.get(Agent, agent.id)
        row.connector_shares = shares
        await db.commit()


def gmail_account(account_id=ACCOUNT):
    return {"id": account_id, "toolkit": {"slug": "gmail"}, "alias": None, "status": "ACTIVE", "user_id": "u1"}


async def direct(sessions, owner, tool_name, args, shares, *, account_id=ACCOUNT, allowed=(ACCOUNT,)):
    sent, _ = composio_tools.clean_arguments(composio_tools.BY_NAME[tool_name], args)
    async with sessions() as db:
        return await composio_service.run_tool(db, owner, lambda key: composio_bridge.make_client(key), composio_tools.BY_NAME[tool_name],
                                               sent, None, account_id=account_id, allowed_account_ids=list(allowed), shares=shares)


async def search(world, args=None):
    seen = {}
    async def handler(ctx, sink, gate):
        seen["answer"] = await ask(ctx, gate, "gmail_fetch_emails", args or {"max_results": 30})
        return TurnOutcome("succeeded", result="ok")
    await one_turn(world, handler)
    return seen["answer"]


async def test_search_returns_only_in_share_threads_and_never_counts_hidden_ones(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, {"gmail": {ACCOUNT: entry(50)}})
    answer = await search(world)
    r = rule(50)
    threads = [row["threadId"] for row in answer["result"]]
    assert threads and all(gmail_share.in_share(r, t) for t in threads)
    assert answer["count"] == len(threads)  # visible rows only
    assert "hidden" not in json.dumps(answer).lower() and "outside" not in json.dumps(answer).lower()


async def test_a_small_share_scans_whole_pages_and_refills_to_the_cap(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, {"gmail": {ACCOUNT: entry(10)}})
    answer = await search(world, {"max_results": 5})
    sizes = [e["arguments"]["max_results"] for e in fake.executes if e.get("slug") == "GMAIL_FETCH_EMAILS"]
    assert len(sizes) > 1 and set(sizes) == {30}      # whole pages, and more than one of them
    assert len(sizes) <= composio_service.MAX_SHARE_PAGES
    assert answer["count"] <= 5 and all(gmail_share.in_share(rule(10), row["threadId"]) for row in answer["result"])


async def test_rows_past_the_ask_are_not_skipped_by_a_next_page_token(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, {"gmail": {ACCOUNT: entry(90)}})
    answer = await search(world, {"max_results": 3})
    assert answer["count"] == 3 and answer["truncated"] is True and "next_page_token" not in answer


async def test_no_share_changes_nothing(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, {})
    answer = await search(world)
    assert answer["count"] == 30 and answer["next_page_token"] == "p1"
    assert [e["arguments"].get("max_results") for e in fake.executes if e.get("slug") == "GMAIL_FETCH_EMAILS"] == [30]


async def test_an_out_of_share_thread_read_never_reaches_composio(sessions, owner, world, fake):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    out = thread_in(rule(5), False)
    shares = {"gmail": {ACCOUNT: entry(5)}}
    answer = await direct(sessions, owner, "gmail_fetch_thread", {"thread_id": out}, shares)
    assert answer["code"] == "outside_share" and fake.executes == []
    inside = await direct(sessions, owner, "gmail_fetch_thread", {"thread_id": thread_in(rule(5))}, shares)
    assert inside["count"] == 1


async def test_an_out_of_share_message_is_read_but_never_returned(sessions, owner, world, fake):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    out = thread_in(rule(5), False)
    answer = await direct(sessions, owner, "gmail_fetch_message", {"message_id": f"m_{out}"}, {"gmail": {ACCOUNT: entry(5)}})
    assert answer["code"] == "outside_share" and SECRET not in json.dumps(answer)
    assert len(fake.executes) == 1


async def test_a_draft_into_an_out_of_share_thread_is_refused(sessions, owner, world, fake):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    out = thread_in(rule(5), False)
    args = {"thread_id": out, "recipient_email": "c@example.com", "subject": "Re: help", "body": "Hello"}
    answer = await direct(sessions, owner, "gmail_create_draft_reply", args, {"gmail": {ACCOUNT: entry(5)}})
    assert answer["code"] == "outside_share" and fake.executes == []


async def test_send_email_is_denied_while_a_share_is_set_and_allowed_without_one(sessions, owner, world, fake):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    args = {"recipient_email": "c@example.com", "subject": "Hi", "body": "Hello"}
    blocked = await direct(sessions, owner, "gmail_send_email", args, {"gmail": {ACCOUNT: entry(50)}})
    assert blocked["code"] == "share_blocks_send" and fake.executes == []
    free = await direct(sessions, owner, "gmail_send_email", args, {"gmail": {ACCOUNT: entry(100)}})
    assert "error" not in free and len(fake.executes) == 1


async def test_profile_totals_are_removed_under_a_share(sessions, owner, world, fake):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    shared = await direct(sessions, owner, "gmail_get_profile", {}, {"gmail": {ACCOUNT: entry(50)}})
    assert shared["result"]["emailAddress"] == "owner@example.com" and "messagesTotal" not in shared["result"] and "threadsTotal" not in shared["result"]
    plain = await direct(sessions, owner, "gmail_get_profile", {}, {})
    assert plain["result"]["messagesTotal"] == 98765


async def test_two_accounts_use_their_own_rules(sessions, owner, world, fake):
    runtime, engine, agent = world
    fake.accounts += [gmail_account("ca_g1"), gmail_account("ca_g2")]
    shares = {"gmail": {"ca_g1": entry(5)}}  # ca_g2 has no rule: the whole mailbox
    out = thread_in(rule(5), False)
    one = await direct(sessions, owner, "gmail_fetch_thread", {"thread_id": out}, shares, account_id="ca_g1", allowed=("ca_g1", "ca_g2"))
    two = await direct(sessions, owner, "gmail_fetch_thread", {"thread_id": out}, shares, account_id="ca_g2", allowed=("ca_g1", "ca_g2"))
    assert one["code"] == "outside_share" and two["count"] == 1


@pytest.mark.parametrize("shares", [{"gmail": {ACCOUNT: {"percent": 0}}}, {"gmail": {ACCOUNT: "x"}}, {"gmail": "x"}, "x"])
async def test_a_damaged_rule_denies_reads_and_never_widens(sessions, owner, world, fake, shares):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    answer = await direct(sessions, owner, "gmail_fetch_emails", {}, shares)
    assert answer["code"] == "share_invalid" and fake.executes == []


async def test_a_damaged_rule_for_another_account_does_not_block_this_one(sessions, owner, world, fake):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    answer = await direct(sessions, owner, "gmail_fetch_emails", {}, {"gmail": {"someone_else": {"percent": 0}}})
    assert answer["count"] == 30


async def test_lowering_the_share_applies_to_the_next_call_in_the_same_turn(world, fake, sessions):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    wide, narrow = rule(50), rule(5)
    thread = next(f"th{n}" for n in range(100_000) if gmail_share.in_share(wide, f"th{n}") and not gmail_share.in_share(narrow, f"th{n}"))
    await grant(sessions, agent, {"gmail": {ACCOUNT: entry(50)}})
    seen = {}
    async def handler(ctx, sink, gate):
        seen["before"] = await ask(ctx, gate, "gmail_fetch_thread", {"thread_id": thread})
        async with sessions() as db:
            (await db.get(Agent, agent.id)).connector_shares = {"gmail": {ACCOUNT: entry(5)}}
            await db.commit()
        seen["after"] = await ask(ctx, gate, "gmail_fetch_thread", {"thread_id": thread})
        return TurnOutcome("succeeded", result="ok")
    await one_turn(world, handler)
    assert seen["before"]["count"] == 1 and seen["after"]["code"] == "outside_share"


async def test_pinned_threads_are_always_shown(sessions, owner, world, fake):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    out = thread_in(rule(5), False)
    answer = await direct(sessions, owner, "gmail_fetch_thread", {"thread_id": out}, {"gmail": {ACCOUNT: entry(5, pins=[out])}})
    assert answer["count"] == 1


def test_the_agent_is_told_about_the_share():
    note = composio_tools.tool_note({"gmail": "read"})
    assert "outside_share" in note and "never guess or state how much mail" in note
    assert "outside_share" not in composio_tools.tool_note({"github": "read"})
