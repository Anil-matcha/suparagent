"""Parse only owner-addressable Discord DMs and approval button interactions."""

# A person's own message is type 0, or type 19 when sent with Discord's Reply
# feature (checked against Discord's message type table, 2026-11-27). An edit is
# a different event (MESSAGE_UPDATE), and other types are system messages.
PERSON_MESSAGE_TYPES = (0, 19)

from dataclasses import dataclass


@dataclass(frozen=True)
class DirectMessage:
    user: str
    channel: str
    text: str
    message_id: str
    is_bot: bool


@dataclass(frozen=True)
class ButtonClick:
    user: str
    channel: str
    message_id: str
    action: str
    approval_id: str
    message_text: str
    message_author_id: str


def parse(event: dict) -> DirectMessage | ButtonClick | None:
    if event.get("op") != 0 or not isinstance(event.get("d"), dict):
        return None
    data = event["d"]
    if event.get("t") == "MESSAGE_CREATE":
        author = data.get("author")
        if (data.get("guild_id") is not None or not isinstance(author, dict)
                or data.get("type") not in PERSON_MESSAGE_TYPES):
            return None
        user, channel, message_id, text = (
            author.get("id"), data.get("channel_id"), data.get("id"), data.get("content"),
        )
        if not all(isinstance(value, str) for value in (user, channel, message_id, text)):
            return None
        return DirectMessage(user, channel, text, message_id, author.get("bot") is True)
    if event.get("t") == "INTERACTION_CREATE" and data.get("type") == 3:
        author = data.get("user")
        component = data.get("data")
        message = data.get("message")
        if (data.get("guild_id") is not None or not isinstance(author, dict)
                or not isinstance(component, dict) or not isinstance(message, dict)
                or component.get("component_type") != 2):
            return None
        custom_id = component.get("custom_id")
        message_author = message.get("author")
        if not isinstance(custom_id, str):
            return None
        action, separator, approval_id = custom_id.partition(":")
        values = (author.get("id"), data.get("channel_id"), message.get("id"),
                  message_author.get("id") if isinstance(message_author, dict) else None)
        if not separator or not approval_id or not all(isinstance(value, str) and value for value in values):
            return None
        return ButtonClick(
            user=values[0], channel=values[1], message_id=values[2],
            action=action, approval_id=approval_id,
            message_text=message.get("content") if isinstance(message.get("content"), str) else "",
            message_author_id=values[3],
        )
    return None
