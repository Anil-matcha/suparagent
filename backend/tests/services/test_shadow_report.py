"""The shadow-mode comparison report (BL-173): predictions set beside what the owner did, with no model call.

Real runtime, real run_tool and share rule, a Gmail fake that serves the threads each test describes."""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, select

from app import gmail_share
from app.db.models import Agent, ShadowPrediction
from app.services import agent_store, composio_bridge, shadow_report
from app.services.composio_client import ComposioClient
from tests.services.test_composio_bridge import world  # noqa: F401
from tests.services.test_composio_gmail_share import ACCOUNT, GmailFake, entry, gmail_account, grant, rule, thread_in
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
ME = "owner@example.com"
CUSTOMER = "Cora Customer <cora@customer.example>"
DRAFT_SENTINEL = "DRAFT_TEXT_THAT_MUST_NEVER_BE_RETURNED"
REPLY_SENTINEL = "REPLY_TEXT_THAT_MUST_NEVER_BE_RETURNED"


class ReportFake(GmailFake):
    """GmailFake with the threads a test describes, the owner's address, and threads whose read fails."""

    def __init__(self):
        super().__init__()
        self.threads: dict[str, list] = {}
        self.broken: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/execute"):
            body = json.loads(request.content)
            if body["tool_slug"] == "GMAIL_FETCH_MESSAGE_BY_THREAD_ID" and body["arguments"].get("thread_id") in self.broken:
                return httpx.Response(500, json={"error": {"message": "boom"}})
        return super().handler(request)

    def gmail(self, slug, args):
        if slug == "GMAIL_GET_PROFILE":
            return {"emailAddress": ME, "messagesTotal": 99999, "threadsTotal": 4444, "historyId": "7"}
        if slug == "GMAIL_FETCH_MESSAGE_BY_THREAD_ID":
            return {"messages": self.threads.get(args["thread_id"], [])}
        return super().gmail(slug, args)


@pytest.fixture
async def fake(monkeypatch):
    fake = ReportFake()
    monkeypatch.setattr(composio_bridge, "make_client", lambda key: ComposioClient(key, transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture(autouse=True)
async def clean(sessions, owner):
    shadow_report._CACHE.clear()
    shadow_report._REFRESHED.clear()
    yield
    async with sessions() as db:
        await db.execute(delete(ShadowPrediction).where(ShadowPrediction.owner_id == owner))
        await db.commit()


def iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


def message(sender, at, text="ok", n=0):
    return {"messageId": f"m{n}", "threadId": "t", "sender": sender, "messageTimestamp": iso(at), "messageText": text}


async def predict(sessions, owner, agent, thread, rank, at, *, category="question", draft=None, turn=None, account=ACCOUNT):
    async with sessions() as db:
        db.add(ShadowPrediction(owner_id=owner, agent_id=agent.id, account_id=account, thread_id=thread, rank=rank,
                                category=category, draft_text=draft, turn_id=turn if turn is not None else abs(hash(thread)) % 10**9,
                                predicted_at=at))
        await db.commit()


async def ready(world, fake, sessions, shares=None):
    runtime, engine, agent = world
    fake.accounts.append(gmail_account())
    await grant(sessions, agent, shares if shares is not None else {})
    return runtime, agent


# -- finding the owner's reply -----------------------------------------------------------------------------------------

def test_owner_reply_is_found_by_the_connected_address():
    at = NOW - timedelta(days=2)
    thread = [
        message(CUSTOMER, at - timedelta(hours=1), n=1),
        message(ME, at - timedelta(minutes=5), "an old answer before the prediction", n=2),   # before: ignored
        message("Not Me <notowner@example.com>", at + timedelta(hours=1), n=3),                # another address: ignored
        message(f"Owner <{ME.upper()}>", at + timedelta(hours=3), "the real reply", n=4),     # matched, case-insensitive
        message(ME, at + timedelta(hours=5), "a later one", n=5),
    ]
    reply = shadow_report.first_owner_reply(thread, ME, at)
    assert reply == (at + timedelta(hours=3), "the real reply")
    assert shadow_report.first_owner_reply(thread[:1], ME, at) is None
    assert shadow_report.address_of("Ada <Ada@Example.COM>") == "ada@example.com" and shadow_report.address_of(5) is None


def test_message_times_read_from_either_field():
    at = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
    assert shadow_report.when_of({"internalDate": str(int(at.timestamp() * 1000))}) == at
    assert shadow_report.when_of({"messageTimestamp": "2026-11-20T09:30:00Z"}) == at
    assert shadow_report.when_of({"messageTimestamp": "nonsense"}) is None and shadow_report.when_of({}) is None


async def test_the_report_finds_the_owner_reply_through_the_mailbox(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    at = NOW - timedelta(days=3)
    await predict(sessions, owner, agent, "th1", 1, at)
    fake.threads["th1"] = [message(CUSTOMER, at - timedelta(hours=1), n=1), message(ME, at + timedelta(hours=2), n=2)]
    report = await shadow_report.build(runtime, agent.id, 28, now=NOW)
    assert report["sample"] == {"predictions": 1, "with_owner_reply": 1, "drafts": 0}
    assert report["accounts"] == [ACCOUNT]
    assert report["agent_id"] == agent.id and report["window_days"] == 28 and report["generated_at"] == NOW.isoformat()


# -- ranking and disagreements -------------------------------------------------------------------------------------------

async def test_ranking_agreement_and_disagreement_list(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    day_a, day_b = NOW - timedelta(days=9), NOW - timedelta(days=8)  # two UTC days, both more than a week ago
    ranks = [1, 2, 3, 4, 11, 12]
    # (rank -> hours until the owner replied; None = never)
    replies = {"A": {1: 2, 2: 2, 3: 2, 4: 2, 11: 0.5, 12: None},
               "B": {1: None, 2: 3, 3: 3, 4: 3, 11: 5, 12: 30}}
    turn = 0
    for label, day in (("A", day_a), ("B", day_b)):
        for index, rank in enumerate(ranks):
            turn += 1
            at = day + timedelta(minutes=index)
            thread = f"{label}-{rank}"
            await predict(sessions, owner, agent, thread, rank, at, turn=turn, category="bug" if rank <= 4 else "other")
            hours = replies[label][rank]
            fake.threads[thread] = [message(CUSTOMER, at - timedelta(hours=1), n=1)] + (
                [message(ME, at + timedelta(hours=hours), n=2)] if hours is not None else [])
    report = await shadow_report.build(runtime, agent.id, 28, now=NOW)
    # The top five of each day: A answers 5 of 5, B answers 4 of 5 (rank 1 never); the baseline is all 12 threads.
    assert report["ranking"]["top5_answered_24h"] == {"rate": 0.9, "n": 10}
    assert report["ranking"]["baseline_answered_24h"] == {"rate": 0.75, "n": 12}
    assert report["ranking"]["too_few"] is False
    kinds = [(d["thread_id"], d["kind"], d["rank"]) for d in report["disagreements"]]
    assert kinds == [("B-1", "ranked_high_unanswered", 1), ("A-11", "answered_fast_ranked_low", 11)]
    assert report["disagreements"][0]["category"] == "bug" and report["disagreements"][1]["category"] == "other"
    assert report["sample"] == {"predictions": 12, "with_owner_reply": 10, "drafts": 0}


async def test_a_thread_too_new_to_judge_is_not_counted_in_the_ranking_rates(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    for n in range(12):
        await predict(sessions, owner, agent, f"old{n}", n + 1, NOW - timedelta(days=3, minutes=n), turn=n)
    await predict(sessions, owner, agent, "fresh", 1, NOW - timedelta(hours=2), turn=99)  # not yet 24 hours old
    report = await shadow_report.build(runtime, agent.id, 28, now=NOW)
    assert report["sample"]["predictions"] == 13
    assert report["ranking"]["baseline_answered_24h"]["n"] == 12  # "fresh" cannot be judged yet


# -- drafts ------------------------------------------------------------------------------------------------------------

def test_draft_classes_at_the_thresholds():
    assert shadow_report.draft_class(0.9) == "as_is" and shadow_report.draft_class(1.0) == "as_is"
    assert shadow_report.draft_class(0.8999) == "light_edit" and shadow_report.draft_class(0.6) == "light_edit"
    assert shadow_report.draft_class(0.5999) == "rewrite" and shadow_report.draft_class(0.0) == "rewrite"
    base = "alpha bravo charlie delta echo foxtrot golf hotel india"                  # 9 words
    assert shadow_report.similarity(base, base) == 1.0
    assert shadow_report.similarity(base, base + " juliet") == 0.9                       # 9 shared of 10 in all: as is
    a = "w1 w2 w3 w4 w5 w6 x1 x2"
    b = "w1 w2 w3 w4 w5 w6 y1 y2"
    assert shadow_report.similarity(a, b) == 0.6                                         # 6 shared of 10 in all: light edit
    assert shadow_report.similarity("completely different answer here", base) < 0.6
    assert shadow_report.similarity("", base) == 0.0 and shadow_report.similarity(None, None) == 0.0


def test_quotes_greeting_signoff_and_punctuation_do_not_change_the_score():
    draft = "Thanks for writing. We can ship the fix on Monday."
    reply = ("Hi Cora,\n\nThanks for writing! We can ship the fix on Monday.\n\nBest regards,\nOwner\n\n"
             "On Tue, Cora wrote:\n> my old question\n> and more")
    assert shadow_report.similarity(draft, reply) == 1.0
    assert shadow_report.words_of(">quoted only") == []
    assert len(shadow_report.words_of("w " * 500)) <= shadow_report.COMPARE_WORDS


async def test_draft_classes_and_the_usable_rate_through_the_report(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    base = "alpha bravo charlie delta echo foxtrot golf hotel india"
    light_draft, light_reply = "w1 w2 w3 w4 w5 w6 x1 x2", "w1 w2 w3 w4 w5 w6 y1 y2"
    plan = ([("as_is", base, base)] * 4 + [("light", light_draft, light_reply)] * 3
            + [("rewrite", base, "nothing in common with that draft at all")] * 3 + [("none", base, None)] * 2)
    for n, (_, draft, reply) in enumerate(plan):
        at = NOW - timedelta(days=2, minutes=n)
        thread = f"d{n}"
        await predict(sessions, owner, agent, thread, n + 1, at, draft=draft, turn=n)
        fake.threads[thread] = [message(CUSTOMER, at - timedelta(hours=1), n=1)] + (
            [message(ME, at + timedelta(hours=1), reply, n=2)] if reply else [])
    report = await shadow_report.build(runtime, agent.id, 28, now=NOW)
    assert report["drafts"] == {"as_is": 4, "light_edit": 3, "rewrite": 3, "no_reply_seen": 2, "usable_rate": 0.7, "too_few": False}
    assert report["sample"]["drafts"] == 12


async def test_no_trend_under_ten(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    for n in range(5):
        at = NOW - timedelta(days=2, minutes=n)
        await predict(sessions, owner, agent, f"s{n}", n + 1, at, draft="same words in both", turn=n)
        fake.threads[f"s{n}"] = [message(ME, at + timedelta(hours=1), "same words in both", n=1)]
    report = await shadow_report.build(runtime, agent.id, 28, now=NOW)
    assert report["drafts"]["as_is"] == 5 and report["drafts"]["too_few"] is True and report["drafts"]["usable_rate"] is None
    assert report["ranking"]["too_few"] is True
    assert report["ranking"]["top5_answered_24h"]["rate"] is None and report["ranking"]["baseline_answered_24h"]["rate"] is None


async def test_no_reply_seen_is_counted_not_guessed(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    for n in range(3):
        at = NOW - timedelta(days=2, minutes=n)
        await predict(sessions, owner, agent, f"n{n}", n + 1, at, draft="a draft nobody answered", turn=n)
        fake.threads[f"n{n}"] = [message(CUSTOMER, at + timedelta(hours=1), "still waiting", n=1)]  # only the customer wrote
    report = await shadow_report.build(runtime, agent.id, 28, now=NOW)
    assert report["drafts"]["no_reply_seen"] == 3 and report["drafts"]["as_is"] == report["drafts"]["rewrite"] == 0
    assert report["sample"]["with_owner_reply"] == 0 and report["sample"]["drafts"] == 3


# -- the share of the mailbox, failures, privacy ---------------------------------------------------------------------------

async def test_a_thread_the_share_now_hides_is_skipped_silently(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions, {"gmail": {ACCOUNT: entry(5)}})
    inside, hidden = thread_in(rule(5)), thread_in(rule(5), False)
    for n, thread in enumerate((inside, hidden)):
        await predict(sessions, owner, agent, thread, n + 1, NOW - timedelta(days=2, minutes=n), turn=n)
    report = await shadow_report.build(runtime, agent.id, 28, now=NOW)
    assert report["sample"]["predictions"] == 1
    text = json.dumps(report)
    assert hidden not in text and "hidden" not in text.lower() and "outside" not in text.lower()


async def test_a_failed_read_does_not_fail_the_report(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    for n in range(3):
        await predict(sessions, owner, agent, f"f{n}", n + 1, NOW - timedelta(days=2, minutes=n), turn=n)
    fake.broken.add("f1")
    report = await shadow_report.build(runtime, agent.id, 28, now=NOW)
    assert report["sample"]["predictions"] == 2


async def test_an_agent_without_gmail_gets_an_empty_report_and_a_stranger_gets_none(world, fake, sessions, owner):
    runtime, engine, agent = world
    await predict(sessions, owner, agent, "x", 1, NOW - timedelta(days=2), turn=1)
    report = await shadow_report.build(runtime, agent.id, 28, now=NOW)  # the world agent only has github
    assert report["sample"] == {"predictions": 0, "with_owner_reply": 0, "drafts": 0} and report["ranking"]["too_few"] is True
    assert await shadow_report.build(runtime, 987654321, 28, now=NOW) is None
    async with sessions() as db:
        theirs = await agent_store.create_agent(db, owner_id="someone-else", name="X")
    try:
        assert await shadow_report.build(runtime, theirs.id, 28, now=NOW) is None
    finally:
        async with sessions() as db:
            await db.execute(delete(Agent).where(Agent.id == theirs.id))
            await db.commit()


async def test_the_response_never_contains_draft_or_reply_text(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    at = NOW - timedelta(days=2)
    await predict(sessions, owner, agent, "p1", 1, at, draft=DRAFT_SENTINEL + " words", turn=1)
    fake.threads["p1"] = [message(ME, at + timedelta(hours=1), REPLY_SENTINEL + " words", n=1)]
    report = await shadow_report.build(runtime, agent.id, 28, now=NOW)
    text = json.dumps(report)
    assert DRAFT_SENTINEL not in text and REPLY_SENTINEL not in text and "draft_text" not in text


# -- cache and refresh --------------------------------------------------------------------------------------------------------

async def test_a_report_is_kept_ten_minutes_and_a_refresh_is_allowed_once_a_minute(world, fake, sessions, owner, monkeypatch):
    runtime, agent = await ready(world, fake, sessions)
    await predict(sessions, owner, agent, "c1", 1, NOW - timedelta(days=2), turn=1)
    clock = {"t": 1000.0}
    monkeypatch.setattr(shadow_report, "_clock", lambda: clock["t"])

    def reads():
        return len([e for e in fake.executes if e.get("slug") == "GMAIL_FETCH_MESSAGE_BY_THREAD_ID"])

    await shadow_report.build(runtime, agent.id, 28, now=NOW)
    assert reads() == 1
    clock["t"] += 30
    await shadow_report.build(runtime, agent.id, 28, now=NOW)                      # kept: no new read
    assert reads() == 1
    await shadow_report.build(runtime, agent.id, 28, refresh=True, now=NOW)        # first refresh: allowed
    assert reads() == 2
    clock["t"] += 30
    await shadow_report.build(runtime, agent.id, 28, refresh=True, now=NOW)        # under a minute later: the kept report
    assert reads() == 2
    clock["t"] += 31
    await shadow_report.build(runtime, agent.id, 28, refresh=True, now=NOW)        # a minute has passed
    assert reads() == 3
    clock["t"] += shadow_report.CACHE_SECONDS + 1
    await shadow_report.build(runtime, agent.id, 28, now=NOW)                      # older than ten minutes
    assert reads() == 4
    await shadow_report.build(runtime, agent.id, 14, now=NOW)                      # another window is another report
    assert reads() == 5


async def test_changing_the_share_never_serves_a_report_made_under_the_old_one(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    await predict(sessions, owner, agent, "c1", 1, NOW - timedelta(days=2), turn=1)
    await shadow_report.build(runtime, agent.id, 28, now=NOW)
    before = len(fake.executes)
    async with sessions() as db:
        (await db.get(Agent, agent.id)).connector_shares = {"gmail": {ACCOUNT: entry(50)}}
        await db.commit()
    await shadow_report.build(runtime, agent.id, 28, now=NOW)
    assert len(fake.executes) > before


# -- retention ----------------------------------------------------------------------------------------------------------------

async def test_drafts_older_than_ninety_days_lose_their_text_and_keep_their_digest(world, fake, sessions, owner):
    runtime, agent = await ready(world, fake, sessions)
    async with sessions() as db:
        for n, age in enumerate((100, 10)):
            db.add(ShadowPrediction(owner_id=owner, agent_id=agent.id, account_id=ACCOUNT, thread_id=f"r{n}", rank=1,
                                    category="bug", draft_text=f"draft {n}", draft_digest=f"digest{n}", turn_id=n,
                                    predicted_at=NOW - timedelta(days=age)))
        await db.commit()
    async with sessions() as db:
        assert await shadow_report.purge_old_drafts(db, now=NOW) == 1
    async with sessions() as db:
        rows = {r.thread_id: r for r in (await db.execute(select(ShadowPrediction).where(ShadowPrediction.owner_id == owner))).scalars()}
    assert rows["r0"].draft_text is None and rows["r0"].draft_digest == "digest0"
    assert rows["r1"].draft_text == "draft 1"
    # every computation purges first
    async with sessions() as db:
        db.add(ShadowPrediction(owner_id=owner, agent_id=agent.id, account_id=ACCOUNT, thread_id="r2", rank=1, category="bug",
                                draft_text="old", turn_id=5, predicted_at=NOW - timedelta(days=95)))
        await db.commit()
    await shadow_report.build(runtime, agent.id, 28, now=NOW)
    async with sessions() as db:
        assert (await db.execute(select(ShadowPrediction.draft_text).where(ShadowPrediction.thread_id == "r2"))).scalar_one() is None



async def test_the_newest_draft_of_a_thread_is_used_even_when_a_later_mark_has_none(world, fake, sessions, owner):
    """Triage and drafts run in different turns: the newest row for a thread may carry no draft."""
    runtime, agent = await ready(world, fake, sessions)
    text = "thank you for writing in we have fixed the problem on our side"
    for n in range(10):
        at = NOW - timedelta(days=2, minutes=n)
        thread = f"k{n}"
        await predict(sessions, owner, agent, thread, n + 1, at, draft=text, turn=100 + n)                 # the draft
        await predict(sessions, owner, agent, thread, n + 1, at + timedelta(minutes=30), turn=200 + n)     # a later re-mark, no draft
        fake.threads[thread] = [message(ME, at + timedelta(hours=2), text, n=1)]
    report = await shadow_report.build(runtime, agent.id, 28, now=NOW)
    assert report["sample"]["predictions"] == 10                       # still one view per thread
    assert report["drafts"]["as_is"] == 10 and report["sample"]["drafts"] == 10
