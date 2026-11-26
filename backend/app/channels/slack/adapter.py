"""Slack as the bridge's chat app: posts to the owner's DM, shows approvals as
Block Kit cards with Allow and Deny buttons, and replaces a card with its
outcome once answered."""

from app.channels.base import ApprovalCard
from app.channels.slack.api import SlackApi
from app.channels.slack.mrkdwn import escape, to_mrkdwn

ACTION_ALLOW = "approval_allow"
ACTION_DENY = "approval_deny"
# What one section block may hold is 3,000 characters; leave room for the
# wording around the request.
CARD_CHARS = 2_600
MAX_CHARS = 3_500
TITLE_CHARS = 200


class SlackAdapter:
    name = "Slack"
    max_chars = MAX_CHARS
    card_chars = CARD_CHARS

    def __init__(self, api: SlackApi, channel: str) -> None:
        self._api = api
        self._channel = channel

    def render(self, text: str) -> str:
        return to_mrkdwn(text)

    async def send_text(self, text: str) -> None:
        await self._api.post_message(self._channel, text)

    async def send_approval(self, card: ApprovalCard) -> object | None:
        title = _title(card)
        body = f"*{escape(card.tool)}* wants to run this:"
        if card.summary:
            body += "\n" + escape(card.summary)
        if card.detail:
            # A fence inside the request must not end ours early.
            body += "\n```" + escape(card.detail).replace("```", "'''") + "```"
        interactive = card.interactive
        if len(body) > CARD_CHARS:
            # Escaping can expand a request that fitted the bridge's raw-text
            # limit. Never show buttons on a shortened approval request.
            body = "The request is too long to review in Slack. Open the app to answer it."
            interactive = False
        blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": body}}]
        if interactive:
            blocks.append({
                "type": "actions",
                "elements": [
                    {"type": "button", "action_id": ACTION_ALLOW, "value": card.approval_id,
                     "style": "primary", "text": {"type": "plain_text", "text": "Allow"}},
                    {"type": "button", "action_id": ACTION_DENY, "value": card.approval_id,
                     "style": "danger", "text": {"type": "plain_text", "text": "Deny"}},
                ],
            })
        else:
            blocks.append({"type": "context", "elements": [
                {"type": "mrkdwn", "text": "This one is too big to review here. Open the app to answer it."}]})
        posted = await self._api.post_message(self._channel, f"Approval needed: {title}", blocks=blocks)
        ts, channel = posted.get("ts"), posted.get("channel")
        if isinstance(ts, str) and isinstance(channel, str):
            return (channel, ts, title)
        return None

    async def close_approval(self, handle: object | None, outcome: str) -> None:
        if not (isinstance(handle, tuple) and len(handle) == 3):
            return
        channel, ts, title = handle
        await self.replace_card(channel, ts, title, outcome)

    async def replace_card(self, channel: str, ts: str, title: str, outcome: str) -> None:
        line = f"{escape(title)}\n_{escape(outcome)}_"
        await self._api.update_message(
            channel, ts, f"{title}: {outcome}",
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": line}}],
        )


def _title(card: ApprovalCard) -> str:
    text = f"{card.tool}: {card.summary}" if card.summary else card.tool
    text = " ".join(text.split())
    return text if len(text) <= TITLE_CHARS else text[: TITLE_CHARS - 1] + "…"
