"""What a chat app must provide to the bridge."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ApprovalCard:
    """One question for the owner: an agent wants to run a tool.

    `interactive` is False when the whole request does not fit one card; the
    adapter then shows no buttons and the text says to open the app."""

    approval_id: str
    tool: str
    summary: str
    detail: str
    interactive: bool


class ChannelAdapter(Protocol):
    # Shown to the person ("Slack") and recorded as who answered an approval.
    name: str
    # The most characters one message may carry (the bridge cuts replies to it
    # after `render`).
    max_chars: int
    # The most characters an approval card may carry before it loses its buttons.
    card_chars: int

    def render(self, text: str) -> str:
        """The reply's Markdown in the chat app's own markup."""

    async def send_text(self, text: str) -> None:
        """Post one message to the owner. Raises on failure."""

    async def send_approval(self, card: ApprovalCard) -> object | None:
        """Post an approval card; the returned handle is given back to
        `close_approval` (None when nothing can be updated later)."""

    async def close_approval(self, handle: object | None, outcome: str) -> None:
        """Replace the card with a final line ("Allowed", "Denied"...)."""
