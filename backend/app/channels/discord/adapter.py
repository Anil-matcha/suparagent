"""Render channel replies and approval buttons in a Discord direct message."""

from app.channels.base import ApprovalCard
from app.channels.discord.api import DiscordApi

ACTION_ALLOW = "approval_allow"
ACTION_DENY = "approval_deny"
MAX_CHARS = 1_900
CARD_CHARS = 1_600


class DiscordAdapter:
    name = "Discord"
    max_chars = MAX_CHARS
    card_chars = CARD_CHARS

    def __init__(self, api: DiscordApi, channel: str) -> None:
        self._api, self._channel = api, channel

    def render(self, text: str) -> str:
        return text

    async def send_text(self, text: str) -> None:
        await self._api.post_message(self._channel, text)

    async def send_approval(self, card: ApprovalCard) -> object | None:
        title = _title(card)
        body = f"Approval needed: **{card.tool}** wants to run this:"
        if card.summary:
            body += "\n" + card.summary
        if card.detail:
            body += "\n```\n" + card.detail.replace("```", "'''") + "\n```"
        interactive = card.interactive and len(body) <= CARD_CHARS
        if not interactive:
            body = f"Approval needed: {title}\nThis request is too long to review here. Open the app to answer it."
        components = [{
            "type": 1,
            "components": [
                {"type": 2, "style": 3, "label": "Allow",
                 "custom_id": f"{ACTION_ALLOW}:{card.approval_id}"},
                {"type": 2, "style": 4, "label": "Deny",
                 "custom_id": f"{ACTION_DENY}:{card.approval_id}"},
            ],
        }] if interactive else []
        posted = await self._api.post_message(self._channel, body, components=components)
        message_id, channel = posted.get("id"), posted.get("channel_id")
        if isinstance(message_id, str) and isinstance(channel, str):
            return channel, message_id, title
        return None

    async def close_approval(self, handle: object | None, outcome: str) -> None:
        if not (isinstance(handle, tuple) and len(handle) == 3):
            return
        await self.replace_card(*handle, outcome)

    async def replace_card(self, channel: str, message_id: str, title: str, outcome: str) -> None:
        await self._api.edit_message(channel, message_id, f"{title}\n*{outcome}*", components=[])


def _title(card: ApprovalCard) -> str:
    text = " ".join((f"{card.tool}: {card.summary}" if card.summary else card.tool).split())
    return text[:197] + "…" if len(text) > 198 else text
