"""The pure sampling rule (app/gmail_share.py): stable, monotone, fail closed."""
import pytest

from app import composio_tools, gmail_share
from app.gmail_share import ShareInvalid, ShareRule, in_share, rule_for, rule_from_json

SEED = "00112233445566778899aabbccddeeff"
SEARCH = composio_tools.BY_NAME["gmail_fetch_emails"]


def rule(percent, seed=SEED, pins=()):
    return rule_from_json({"percent": percent, "seed": seed, "pins": list(pins)})


def ids(n=10_000):
    return [f"thread{i:06d}" for i in range(n)]


def test_percent_is_monotone_raising_only_adds():
    small, big = rule(5), rule(20)
    shown_small = {t for t in ids() if in_share(small, t)}
    shown_big = {t for t in ids() if in_share(big, t)}
    assert shown_small < shown_big  # a strict subset: nothing the owner saw is ever hidden by raising


def test_pins_are_always_in():
    pinned = rule(1, pins=["pinned1"])
    assert in_share(pinned, "pinned1")
    assert in_share(rule(1), "pinned1") in (True, False)  # without the pin it is just the hash


def test_same_thread_same_answer_every_call():
    r = rule(10)
    assert [in_share(r, t) for t in ids(500)] == [in_share(r, t) for t in ids(500)]


@pytest.mark.parametrize("percent", [5, 10, 50])
def test_ten_thousand_ids_land_near_the_percent(percent):
    got = sum(in_share(rule(percent), t) for t in ids())
    assert abs(got - percent * 100) < 150  # within 1.5 points


def test_different_seeds_pick_different_threads():
    other = "ffeeddccbbaa99887766554433221100"
    a = {t for t in ids(3000) if in_share(rule(10), t)}
    b = {t for t in ids(3000) if in_share(rule(10, other), t)}
    assert a != b and len(a & b) < len(a) * 0.5


def test_no_rule_and_one_hundred_percent_mean_the_whole_mailbox():
    assert rule_from_json(None) is None and rule_from_json({}) is None
    assert rule(100) is None
    assert in_share(None, "anything") and in_share(None, None)


@pytest.mark.parametrize("bad", [None, "", 5, [], {}, ("x",), True])
def test_a_missing_or_odd_thread_id_is_out(bad):
    assert in_share(rule(50), bad) is False


@pytest.mark.parametrize("value", [
    "x", [], {"percent": 0, "seed": SEED}, {"percent": 101, "seed": SEED}, {"percent": True, "seed": SEED},
    {"percent": 5.5, "seed": SEED}, {"percent": 5}, {"percent": 5, "seed": "zz" * 16}, {"percent": 5, "seed": "ab"},
    {"percent": 5, "seed": SEED, "pins": "a"}, {"percent": 5, "seed": SEED, "pins": [1]}, {"percent": 5, "seed": SEED, "pins": [""]},
    {"percent": 5, "seed": SEED, "pins": ["a"] * 201}, {"percent": 5, "seed": SEED, "pins": ["a" * 201]},
])
def test_a_malformed_rule_raises_share_invalid(value):
    with pytest.raises(ShareInvalid):
        rule_from_json(value)


def test_rule_for_reads_only_this_account_and_fails_closed_on_damage():
    shares = {"gmail": {"a1": {"percent": 5, "seed": SEED, "pins": []}, "a2": {"percent": 0}}}
    assert isinstance(rule_for(shares, "gmail", "a1"), ShareRule)
    assert rule_for(shares, "gmail", "a3") is None          # no entry: whole mailbox
    assert rule_for(shares, "github", "a1") is None
    assert rule_for({}, "gmail", "a1") is None and rule_for(None, "gmail", "a1") is None
    with pytest.raises(ShareInvalid):
        rule_for(shares, "gmail", "a2")                      # a damaged entry for THIS account denies
    for damaged in ("x", {"gmail": "x"}, {"gmail": []}):
        with pytest.raises(ShareInvalid):
            rule_for(damaged, "gmail", "a1")


def test_precheck_refuses_thread_tools_outside_and_all_sends_under_a_rule():
    r = rule(5)
    out = next(t for t in ids() if not in_share(r, t))
    inside = next(t for t in ids() if in_share(r, t))
    for tool in ("gmail_fetch_thread", "gmail_create_draft_reply", "gmail_prepare_checked_reply"):
        assert gmail_share.precheck(tool, {"thread_id": out}, r)["code"] == "outside_share"
        assert gmail_share.precheck(tool, {"thread_id": inside}, r) is None
        assert gmail_share.precheck(tool, {}, r)["code"] == "outside_share"
    assert gmail_share.precheck("gmail_send_email", {}, r)["code"] == "share_blocks_send"
    assert gmail_share.precheck("gmail_fetch_emails", {}, r) is None
    assert gmail_share.precheck("gmail_send_email", {}, None) is None  # no rule: unchanged


def test_split_rows_runs_on_the_raw_answer_and_counts_only_visible_rows():
    r = rule(30)
    rows = [{"threadId": t, "messageId": f"m{n}"} for n, t in enumerate(ids(200))]
    kept, page, token = gmail_share.split_rows(SEARCH, {"messages": rows, "nextPageToken": "NEXT"}, r)
    assert page == 200 and token == "NEXT"
    assert 0 < len(kept) < 200 and all(in_share(r, row["threadId"]) for row in kept)


def test_split_rows_drops_rows_without_a_thread_id():
    r = rule(100 - 1)
    kept, page, _ = gmail_share.split_rows(SEARCH, {"messages": [{"messageId": "m1"}, {"threadId": None}, "junk"]}, r)
    assert kept == [] and page == 3


def test_an_unreadable_search_answer_is_an_error_not_a_pass_through():
    with pytest.raises(composio_tools.BadArguments):
        gmail_share.split_rows(SEARCH, {"surprise": 1}, rule(5))


def test_message_and_profile_helpers():
    r = rule(5)
    out = next(t for t in ids() if not in_share(r, t))
    assert gmail_share.message_in_share({"result": {"threadId": out}}, r) is False
    assert gmail_share.message_in_share({"result": {}}, r) is False
    assert gmail_share.message_in_share({"result": {"threadId": out}}, None) is True
    profile = {"result": {"emailAddress": "a@b.c", "messagesTotal": 9, "threadsTotal": 4, "historyId": "1"}}
    assert gmail_share.strip_profile(dict(profile, result=dict(profile["result"])), r)["result"] == {"emailAddress": "a@b.c", "historyId": "1"}
    assert "messagesTotal" in gmail_share.strip_profile({"result": dict(profile["result"])}, None)["result"]
