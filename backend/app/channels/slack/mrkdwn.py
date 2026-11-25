"""A reply's Markdown as Slack's own markup (mrkdwn).

Slack differs from Markdown in a few ways that matter for an agent's answer:
bold is `*x*`, italic `_x_`, strike `~x~`, links `<url|text>`, there are no
headings, lists or tables, and `&`, `<` and `>` must be escaped. Escaping also
keeps an answer from pinging anyone: `<!channel>` and `<@U123>` arrive as plain
text. Code (fenced and inline) is kept as it is apart from that escaping, and
a table becomes a monospace block because Slack cannot draw one.
"""

import re

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_BULLET = re.compile(r"^([ \t]*)[-*+][ \t]+", re.MULTILINE)
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_ITALIC = re.compile(r"(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.DOTALL)


def to_mrkdwn(markdown: str) -> str:
    text = markdown.replace("\r\n", "\n")
    kept: list[str] = []

    def keep(piece: str) -> str:
        kept.append(piece)
        return f"\x00{len(kept) - 1}\x00"

    text = _FENCE.sub(lambda m: keep(_escape(m.group(0))), text)
    text = _tables(text, keep)
    text = _INLINE_CODE.sub(lambda m: keep(_escape(m.group(0))), text)
    text = _escape(text)
    text = _LINK.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", text)
    text = _HEADING.sub(lambda m: "**" + m.group(1).replace("**", "") + "**", text)
    text = _BULLET.sub(lambda m: m.group(1) + "• ", text)
    bold: list[str] = []

    def hold_bold(match: re.Match) -> str:
        bold.append(match.group(2))
        return f"\x01{len(bold) - 1}\x01"

    text = _BOLD.sub(hold_bold, text)
    text = _ITALIC.sub(r"_\1_", text)
    text = _STRIKE.sub(r"~\1~", text)
    text = re.sub(r"\x01(\d+)\x01", lambda m: f"*{bold[int(m.group(1))]}*", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tables(text: str, keep) -> str:
    """Each run of table rows becomes one code block, without the |---| rule."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not _TABLE_ROW.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and _TABLE_ROW.match(lines[j]):
            j += 1
        rows = [row.strip() for row in lines[i:j] if not _TABLE_RULE.match(row)]
        out.append(keep(_escape("```\n" + "\n".join(rows) + "\n```")))
        i = j
    return "\n".join(out)
