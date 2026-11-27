"""Cutting a long reply into messages a chat app accepts."""

FENCE = "```"
# Room kept for closing and reopening a code fence at a cut.
_FENCE_ROOM = len(FENCE) * 2 + 2


def chunk_text(text: str, limit: int) -> list[str]:
    """`text` as pieces of at most `limit` characters, cut at a paragraph, then
    a line, then a sentence, then a space, and only at last anywhere. A cut
    inside a code fence closes the fence and reopens it in the next piece.
    No piece is empty."""
    if limit < 50:
        raise ValueError("limit is too small to cut text sensibly")
    text = text.strip()
    pieces: list[str] = []
    reopen = False
    while text:
        room = limit - (len(FENCE) + 1 if reopen else 0)
        if len(text) <= room:
            body, text = text, ""
        else:
            cut = _cut_at(text, room - _FENCE_ROOM)
            body, text = text[:cut].rstrip(), text[cut:].lstrip("\n ")
        if reopen:
            body = FENCE + "\n" + body
        inside = body.count(FENCE) % 2 == 1
        if inside and text:
            body += "\n" + FENCE
        reopen = inside and bool(text)
        if body.strip():
            pieces.append(body)
    return pieces


def _cut_at(text: str, size: int) -> int:
    window = text[:size]
    for marker, keep in (("\n\n", 0), ("\n", 0), (". ", 1), ("! ", 1), ("? ", 1), (" ", 0)):
        at = window.rfind(marker)
        if at >= size // 3:
            return at + keep if keep else at
    return size
