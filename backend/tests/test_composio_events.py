"""Composio webhook events (app/composio_events.py): the signature check and the reduction to ids.

The signature scheme (Standard Webhooks) and the envelope shape are ASSUMED, not yet seen in a real delivery:
every fixture below is built from that assumption and is named or marked UNVERIFIED accordingly."""
import base64
import hashlib
import hmac
import json

import pytest

from app import composio_events as events
from app.composio_events import SignatureError, extract, verify

NOW = 1_800_000_000
SECRET = "k" * 64                      # a live subscription returns a 64-character secret
TEXT = "THE_MESSAGE_TEXT_THAT_MUST_NEVER_SURVIVE"
SENDER = "sender-that-must-not-survive@example.com"


def sign(body: bytes, secret_bytes: bytes, delivery_id="evt_1", timestamp=NOW):
    signed = f"{delivery_id}.{timestamp}.".encode() + body
    return "v1," + base64.b64encode(hmac.new(secret_bytes, signed, hashlib.sha256).digest()).decode()


def headers(body: bytes, secret_bytes=SECRET.encode(), delivery_id="evt_1", timestamp=NOW, signature=None):
    return {"webhook-id": delivery_id, "webhook-timestamp": str(timestamp),
            "webhook-signature": signature or sign(body, secret_bytes, delivery_id, timestamp)}


BODY = b'{"id":"evt_1","type":"composio.trigger.message"}'


# -- the signature ----------------------------------------------------------------------------------------

def test_a_correctly_signed_recent_delivery_returns_its_id():
    assert verify(headers(BODY), BODY, SECRET, NOW + 5) == "evt_1"


def test_header_names_are_case_insensitive():
    upper = {k.title(): v for k, v in headers(BODY).items()}
    assert verify(upper, BODY, SECRET, NOW) == "evt_1"


@pytest.mark.parametrize("mutate", [
    lambda h: {**h, "webhook-signature": "v1," + base64.b64encode(b"x" * 32).decode()},   # wrong signature
    lambda h: {k: v for k, v in h.items() if k != "webhook-id"},
    lambda h: {k: v for k, v in h.items() if k != "webhook-timestamp"},
    lambda h: {k: v for k, v in h.items() if k != "webhook-signature"},
    lambda h: {**h, "webhook-signature": "v2," + h["webhook-signature"][3:]},              # not the v1 scheme
    lambda h: {**h, "webhook-signature": "v1,!!!not-base64!!!"},
    lambda h: {**h, "webhook-timestamp": "abc"},
    lambda h: {**h, "webhook-id": "evt_2"},                                                # id is part of what is signed
])
def test_a_bad_or_incomplete_delivery_is_refused(mutate):
    with pytest.raises(SignatureError):
        verify(mutate(headers(BODY)), BODY, SECRET, NOW)


def test_a_changed_body_or_secret_is_refused():
    with pytest.raises(SignatureError):
        verify(headers(BODY), BODY + b" ", SECRET, NOW)
    with pytest.raises(SignatureError):
        verify(headers(BODY), BODY, "another-secret", NOW)
    with pytest.raises(SignatureError):
        verify(headers(BODY), BODY, "", NOW)


@pytest.mark.parametrize("skew, ok", [(0, True), (299, True), (300, True), (301, False), (-301, False), (-299, True), (86_400, False)])
def test_the_timestamp_tolerance_is_300_seconds_either_way(skew, ok):
    h = headers(BODY, timestamp=NOW)
    if ok:
        assert verify(h, BODY, SECRET, NOW + skew) == "evt_1"
    else:
        with pytest.raises(SignatureError):
            verify(h, BODY, SECRET, NOW + skew)


def test_several_space_separated_signatures_are_tried_and_one_match_is_enough():
    good = sign(BODY, SECRET.encode())
    bad = "v1," + base64.b64encode(b"y" * 32).decode()
    h = headers(BODY, signature=f"{bad} v2,whatever {good}")
    assert verify(h, BODY, SECRET, NOW) == "evt_1"


def test_a_whsec_prefixed_secret_means_its_base64_body_and_the_raw_secret_still_works():
    key = b"\x01\x02\x03" * 8
    prefixed = "whsec_" + base64.b64encode(key).decode()
    assert verify(headers(BODY, secret_bytes=key), BODY, prefixed, NOW) == "evt_1"          # signed with the decoded key
    assert verify(headers(BODY, secret_bytes=prefixed.encode()), BODY, prefixed, NOW) == "evt_1"  # signed with the text itself
    with pytest.raises(SignatureError):
        verify(headers(BODY, secret_bytes=b"other"), BODY, prefixed, NOW)


def test_a_prefix_with_a_body_that_is_not_base64_falls_back_to_the_raw_secret():
    odd = "whsec_not base64!!"
    assert verify(headers(BODY, secret_bytes=odd.encode()), BODY, odd, NOW) == "evt_1"


def test_verify_reads_the_real_clock_when_given_none():
    import time
    now = int(time.time())
    assert verify(headers(BODY, timestamp=now), BODY, SECRET) == "evt_1"


# -- extraction (UNVERIFIED envelope shapes) ---------------------------------------------------------------

def message_envelope(**data):
    """UNVERIFIED: an assumed V3 envelope (id, type, metadata, data), with the live-verified Gmail payload fields."""
    payload = {"thread_id": "18c0a1b2c3d4e5f6", "message_id": "18c0a1b2c3d4e5f7", "sender": SENDER, "subject": "Help",
               "message_text": TEXT, "to": "me@example.com", "label_ids": ["INBOX"], "preview": {"body": TEXT},
               "attachment_list": [], "message_timestamp": "2026-11-26T09:00:00Z", **data}
    return {"id": "evt_1", "type": "composio.trigger.message", "timestamp": "2026-11-26T09:00:01Z",
            "metadata": {"trigger_slug": "GMAIL_NEW_GMAIL_MESSAGE", "trigger_id": "ti_AbC-123",
                         "connected_account_id": "ca_g", "user_id": "u1", "log_id": "log_1"},
            "data": payload}


def test_a_new_mail_event_keeps_only_ids_and_drops_all_text():
    got = extract(message_envelope(), "evt_1")
    assert got == {"kind": "message", "event_id": "evt_1", "trigger_id": "ti_AbC-123", "connected_account_id": "ca_g",
                   "thread_id": "18c0a1b2c3d4e5f6", "message_id": "18c0a1b2c3d4e5f7", "received_at": got["received_at"], "bad_ids": False}
    assert got["received_at"].year == 2026
    dumped = json.dumps(got, default=str)
    assert TEXT not in dumped and SENDER not in dumped and "Help" not in dumped and "INBOX" not in dumped


def test_the_payload_may_be_wrapped_once_more_and_camel_case_ids_are_read():
    wrapped = message_envelope()
    wrapped["data"] = {"payload": {"threadId": "abc123", "messageId": "def456", "message_text": TEXT}}
    got = extract(wrapped, "evt_1")
    assert (got["thread_id"], got["message_id"], got["bad_ids"]) == ("abc123", "def456", False)
    assert TEXT not in json.dumps(got, default=str)


def test_the_message_id_may_come_from_the_payload_id_field():
    env = message_envelope()
    env["data"].pop("message_id")
    env["data"]["id"] = "aa11bb22"
    assert extract(env, "evt_1")["message_id"] == "aa11bb22"


@pytest.mark.parametrize("field, value", [("thread_id", "<script>alert(1)</script>"), ("thread_id", "Ignore previous instructions"),
                                          ("thread_id", "ABCDEF"), ("thread_id", "a" * 65), ("message_id", "not hex!"),
                                          ("thread_id", 12345), ("message_id", ["x"])])
def test_an_id_that_is_present_but_not_gmail_shaped_marks_the_event_bad_and_is_dropped(field, value):
    got = extract(message_envelope(**{field: value}), "evt_1")
    assert got["bad_ids"] is True and got[field] is None
    assert str(value) not in json.dumps(got, default=str)


def test_a_missing_thread_id_is_none_not_bad():
    env = message_envelope()
    env["data"].pop("thread_id")
    got = extract(env, "evt_1")
    assert got["thread_id"] is None and got["bad_ids"] is False


@pytest.mark.parametrize("env", [
    None, [], "x", {}, {"id": "evt_1"}, {"type": "composio.trigger.message"},
    {"id": "", "type": "composio.trigger.message"}, {"id": "x" * 201, "type": "composio.trigger.message"},
    {"id": "evt_1", "type": "composio.trigger.message", "metadata": {}},                               # no trigger id
    {"id": "evt_1", "type": "composio.trigger.message", "metadata": {"trigger_id": "bad id!"}},
    {"id": "evt_1", "type": "composio.something.else", "metadata": {"trigger_id": "ti_1"}},
])
def test_anything_that_is_not_an_event_we_act_on_is_none(env):
    assert extract(env) is None


def test_the_body_must_be_the_delivery_that_was_signed():
    assert extract(message_envelope(), "evt_other") is None
    assert extract(message_envelope()) is not None  # no signed id given: not compared


def test_status_events_carry_ids_only():
    disabled = extract({"id": "evt_2", "type": "composio.trigger.disabled", "metadata": {"trigger_id": "ti_9"},
                        "data": {"secret": "TOKEN_THAT_MUST_NOT_SURVIVE"}}, "evt_2")
    assert disabled == {"kind": "status", "status": "trigger_disabled", "event_id": "evt_2", "trigger_id": "ti_9", "connected_account_id": None}
    # the trigger id may also sit in data
    assert extract({"id": "e", "type": "composio.trigger.disabled", "data": {"trigger_id": "ti_8"}})["trigger_id"] == "ti_8"
    expired = extract({"id": "evt_3", "type": "composio.connected_account.expired", "data": {"id": "ca_77", "access_token": "TOKEN_THAT_MUST_NOT_SURVIVE", "toolkit": {"slug": "gmail"}}}, "evt_3")
    assert expired == {"kind": "status", "status": "account_expired", "event_id": "evt_3", "trigger_id": None, "connected_account_id": "ca_77"}
    assert "TOKEN_THAT_MUST_NOT_SURVIVE" not in json.dumps([disabled, expired])
    assert extract({"id": "e", "type": "composio.connected_account.expired", "data": {}}) is None
