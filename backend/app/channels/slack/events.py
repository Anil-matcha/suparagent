"""What a Socket Mode envelope means for the channel.

Only two things matter: a direct message to the app and a click on one of its
buttons. Everything else (channel messages, mentions, reactions, slash commands)
parses to None. This module only reads the shape; whether the sender is the
owner is decided by the caller, from `user` and `team` alone.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DirectMessage:
    user: str
    team: str | None
    channel: str
    text: str
    ts: str | None
    # Slack sets these for a message that is not a plain one a person typed
    # (an edit, a join, another app's post); the caller ignores such messages.
    subtype: str | None = None
    bot_id: str | None = None


@dataclass(frozen=True)
class ButtonClick:
    user: str
    team: str | None
    channel: str
    message_ts: str
    action_id: str
    value: str
    # The text of the message the button is on (a card's fallback line).
    message_text: str = ""


def event_id_of(envelope: dict) -> str | None:
    """Slack's id for an event, the same on a redelivery."""
    payload = envelope.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("event_id"), str):
        return payload["event_id"]
    return None


def parse(envelope: dict) -> DirectMessage | ButtonClick | None:
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return None
    kind = envelope.get("type")
    if kind == "events_api":
        return _direct_message(payload)
    if kind == "interactive":
        return _button_click(payload)
    return None


def _direct_message(payload: dict) -> DirectMessage | None:
    event = payload.get("event")
    if not isinstance(event, dict) or event.get("type") != "message" or event.get("channel_type") != "im":
        return None
    user, channel = event.get("user"), event.get("channel")
    if not isinstance(user, str) or not isinstance(channel, str):
        return None
    text = event.get("text")
    team = payload.get("team_id") if isinstance(payload.get("team_id"), str) else event.get("team")
    return DirectMessage(
        user=user,
        team=team if isinstance(team, str) else None,
        channel=channel,
        text=text if isinstance(text, str) else "",
        ts=event.get("ts") if isinstance(event.get("ts"), str) else None,
        subtype=event.get("subtype") if isinstance(event.get("subtype"), str) else None,
        bot_id=event.get("bot_id") if isinstance(event.get("bot_id"), str) else None,
    )


def _button_click(payload: dict) -> ButtonClick | None:
    if payload.get("type") != "block_actions":
        return None
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    team = payload.get("team") if isinstance(payload.get("team"), dict) else {}
    channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    actions = payload.get("actions")
    action = actions[0] if isinstance(actions, list) and actions and isinstance(actions[0], dict) else {}
    values = (user.get("id"), channel.get("id"), message.get("ts"), action.get("action_id"), action.get("value"))
    if not all(isinstance(v, str) and v for v in values):
        return None
    return ButtonClick(
        user=values[0],
        team=team.get("id") if isinstance(team.get("id"), str) else None,
        channel=values[1],
        message_ts=values[2],
        action_id=values[3],
        value=values[4],
        message_text=message.get("text") if isinstance(message.get("text"), str) else "",
    )
