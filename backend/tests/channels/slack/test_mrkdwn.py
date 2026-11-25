from app.channels.slack.mrkdwn import to_mrkdwn


def test_bold_italic_and_strike():
    assert to_mrkdwn("A **bold** and *italic* and ~~gone~~ word") == "A *bold* and _italic_ and ~gone~ word"


def test_double_underscore_bold_too():
    assert to_mrkdwn("a __bold__ word") == "a *bold* word"


def test_headings_become_bold_lines():
    assert to_mrkdwn("# Title\n\n## Part two ##\ntext") == "*Title*\n\n*Part two*\ntext"


def test_lists_use_bullets_and_numbers_stay():
    assert to_mrkdwn("- one\n* two\n  - nested\n1. first") == "• one\n• two\n  • nested\n1. first"


def test_a_bullet_that_starts_with_bold_is_not_confused_with_italic():
    assert to_mrkdwn("* **Name:** value") == "• *Name:* value"


def test_links_become_slack_links():
    assert to_mrkdwn("See [the docs](https://example.com/a?b=1&c=2).") == "See <https://example.com/a?b=1&amp;c=2|the docs>."


def test_only_web_links_are_converted():
    assert to_mrkdwn("[x](javascript:alert(1))") == "[x](javascript:alert(1))"


def test_special_characters_are_escaped_so_nothing_pings_anyone():
    out = to_mrkdwn("a < b & c > d <!channel> <@U123> <!here>")
    assert out == "a &lt; b &amp; c &gt; d &lt;!channel&gt; &lt;@U123&gt; &lt;!here&gt;"


def test_code_is_kept_as_it_is_apart_from_escaping():
    out = to_mrkdwn("Run `a **b** <c>` now\n\n```python\nx = **y** & <z>\n```")
    assert out == "Run `a **b** &lt;c&gt;` now\n\n```python\nx = **y** &amp; &lt;z&gt;\n```"


def test_a_table_becomes_a_monospace_block_without_its_rule():
    table = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert to_mrkdwn("Before\n" + table + "\nAfter") == "Before\n```\n| a | b |\n| 1 | 2 |\n```\nAfter"


def test_an_unbalanced_star_is_left_alone():
    assert to_mrkdwn("2 * 3 = 6 and a*b") == "2 * 3 = 6 and a*b"


def test_windows_line_endings_are_normalised():
    assert to_mrkdwn("a\r\n**b**") == "a\n*b*"


def test_plain_text_is_unchanged():
    assert to_mrkdwn("Just a sentence.") == "Just a sentence."
