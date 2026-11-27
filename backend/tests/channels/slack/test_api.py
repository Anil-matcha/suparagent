import json

import httpx
import pytest

from app.channels.slack.api import SlackApi, SlackAuthError, SlackError

BOT = "xoxb" + "-1111111111-BOTTOKENBOTTOKEN"
APP = "xapp" + "-1-A111-2222-APPTOKENAPPTOKEN"


class Slack:
    """A fake slack.com: records requests, answers with a script."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.answers: list[httpx.Response] = []

    def __call__(self, request):
        self.requests.append(request)
        return self.answers.pop(0) if self.answers else httpx.Response(200, json={"ok": True})

    def body(self, n=-1):
        return json.loads(self.requests[n].content)


@pytest.fixture
def slack():
    return Slack()


@pytest.fixture
def waits():
    return []


@pytest.fixture
def api(slack, waits):
    async def sleep(seconds):
        waits.append(seconds)

    return SlackApi(BOT, APP, transport=httpx.MockTransport(slack), sleep=sleep)


async def test_auth_test_uses_the_bot_token_and_returns_who_it_is(api, slack):
    slack.answers = [httpx.Response(200, json={"ok": True, "team_id": "T1", "user_id": "U9", "bot_id": "B1", "team": "Acme"})]
    result = await api.auth_test()
    assert (result["team_id"], result["user_id"], result["bot_id"]) == ("T1", "U9", "B1")
    request = slack.requests[0]
    assert str(request.url) == "https://slack.com/api/auth.test"
    assert request.headers["authorization"] == f"Bearer {BOT}"


async def test_open_connection_uses_the_app_token_and_returns_the_address(api, slack):
    slack.answers = [httpx.Response(200, json={"ok": True, "url": "wss://wss-primary.slack.com/link/?ticket=t"})]
    assert await api.open_connection() == "wss://wss-primary.slack.com/link/?ticket=t"
    assert str(slack.requests[0].url) == "https://slack.com/api/apps.connections.open"
    assert slack.requests[0].headers["authorization"] == f"Bearer {APP}"


async def test_an_open_connection_answer_without_an_address_is_a_bad_response(api, slack):
    slack.answers = [httpx.Response(200, json={"ok": True})]
    with pytest.raises(SlackError) as caught:
        await api.open_connection()
    assert caught.value.code == "bad_response"


async def test_post_message_sends_plain_markup_without_link_previews(api, slack):
    slack.answers = [httpx.Response(200, json={"ok": True, "ts": "1.2", "channel": "D1"})]
    result = await api.post_message("D1", "hello *there*")
    assert result["ts"] == "1.2"
    assert slack.body() == {"channel": "D1", "text": "hello *there*", "mrkdwn": True, "unfurl_links": False, "unfurl_media": False}
    assert slack.requests[0].headers["authorization"] == f"Bearer {BOT}"


async def test_post_message_can_carry_blocks_and_update_replaces_them(api, slack):
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "x"}}]
    await api.post_message("D1", "fallback", blocks=blocks)
    assert slack.body()["blocks"] == blocks
    await api.update_message("D1", "1.2", "Allowed")
    assert slack.body() == {"channel": "D1", "ts": "1.2", "text": "Allowed", "blocks": []}
    assert str(slack.requests[-1].url) == "https://slack.com/api/chat.update"


@pytest.mark.parametrize("code", ["invalid_auth", "token_revoked", "account_inactive", "not_allowed_token_type", "missing_scope"])
async def test_a_refused_token_is_an_auth_error(api, slack, code):
    slack.answers = [httpx.Response(200, json={"ok": False, "error": code})]
    with pytest.raises(SlackAuthError) as caught:
        await api.auth_test()
    assert caught.value.code == code


async def test_other_slack_errors_keep_their_code(api, slack):
    slack.answers = [httpx.Response(200, json={"ok": False, "error": "channel_not_found"})]
    with pytest.raises(SlackError) as caught:
        await api.post_message("D1", "x")
    assert caught.value.code == "channel_not_found" and not isinstance(caught.value, SlackAuthError)


async def test_a_rate_limit_is_waited_out_and_retried(api, slack, waits):
    slack.answers = [
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"ok": True, "ts": "9"}),
    ]
    assert (await api.post_message("D1", "x"))["ts"] == "9"
    assert waits == [3.0] and len(slack.requests) == 2


async def test_a_rate_limit_that_does_not_clear_gives_up(api, slack, waits):
    slack.answers = [httpx.Response(429, headers={"Retry-After": "2"}) for _ in range(5)]
    with pytest.raises(SlackError) as caught:
        await api.post_message("D1", "x")
    assert caught.value.code == "rate_limited"
    assert waits == [2.0, 2.0, 2.0]


async def test_a_huge_or_bad_retry_after_is_bounded(api, slack, waits):
    slack.answers = [httpx.Response(429, headers={"Retry-After": "3600"}), httpx.Response(429, headers={"Retry-After": "soon"}), httpx.Response(200, json={"ok": True})]
    await api.post_message("D1", "x")
    assert waits == [30.0, 1.0]


@pytest.mark.parametrize("response,code", [
    (httpx.Response(500), "http_500"),
    (httpx.Response(200, content=b"<html>"), "bad_response"),
    (httpx.Response(200, json=["not", "an", "object"]), "bad_response"),
    (httpx.Response(200, json={"ok": False}), "unknown_error"),
])
async def test_other_failures_are_named_without_leaking_anything(api, slack, response, code):
    slack.answers = [response]
    with pytest.raises(SlackError) as caught:
        await api.auth_test()
    assert caught.value.code == code


async def test_a_network_failure_is_named_and_hides_the_tokens():
    def boom(request):
        raise httpx.ConnectError(f"could not reach slack for {BOT}")

    api = SlackApi(BOT, APP, transport=httpx.MockTransport(boom))
    with pytest.raises(SlackError) as caught:
        await api.auth_test()
    assert caught.value.code == "network"
    assert BOT not in str(caught.value) and APP not in str(caught.value)
    assert caught.value.__cause__ is None
