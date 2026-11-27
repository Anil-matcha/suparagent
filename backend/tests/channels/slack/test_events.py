from app.channels.slack.events import ButtonClick, DirectMessage, event_id_of, parse


def dm(**event):
    base = {"type": "message", "channel_type": "im", "user": "U1", "channel": "D1", "text": "hi", "ts": "1.1"}
    base.update(event)
    return {"type": "events_api", "envelope_id": "e1", "payload": {"event_id": "Ev1", "team_id": "T1", "event": base}}


def click(**changes):
    payload = {
        "type": "block_actions", "user": {"id": "U1"}, "team": {"id": "T1"}, "channel": {"id": "D1"},
        "message": {"ts": "5.5"}, "actions": [{"action_id": "approval_allow", "value": "ap-1"}],
    }
    payload.update(changes)
    return {"type": "interactive", "envelope_id": "e2", "payload": payload}


def test_a_direct_message_is_read():
    assert parse(dm()) == DirectMessage(user="U1", team="T1", channel="D1", text="hi", ts="1.1")


def test_subtype_and_bot_id_are_kept_so_the_caller_can_ignore_them():
    parsed = parse(dm(subtype="message_changed", bot_id="B1"))
    assert parsed.subtype == "message_changed" and parsed.bot_id == "B1"


def test_a_missing_text_is_empty_not_an_error():
    envelope = dm()
    del envelope["payload"]["event"]["text"]
    assert parse(envelope).text == ""


def test_channel_messages_mentions_and_other_events_are_not_direct_messages():
    assert parse(dm(channel_type="channel")) is None
    assert parse(dm(channel_type="group")) is None
    assert parse(dm(type="app_mention")) is None
    assert parse(dm(user=None)) is None
    assert parse({"type": "slash_commands", "payload": {"command": "/x"}}) is None
    assert parse({"type": "events_api", "payload": {}}) is None
    assert parse({"type": "events_api"}) is None


def test_a_button_click_is_read():
    assert parse(click()) == ButtonClick(user="U1", team="T1", channel="D1", message_ts="5.5", action_id="approval_allow", value="ap-1")


def test_a_click_missing_anything_is_ignored():
    assert parse(click(actions=[])) is None
    assert parse(click(user={})) is None
    assert parse(click(message={})) is None
    assert parse(click(type="view_submission")) is None
    assert parse(click(actions=[{"action_id": "a"}])) is None


def test_the_event_id_is_slacks_own():
    assert event_id_of(dm()) == "Ev1"
    assert event_id_of(click()) is None
    assert event_id_of({"payload": "x"}) is None
