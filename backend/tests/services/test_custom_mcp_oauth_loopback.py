"""The desktop OAuth callback listener: 127.0.0.1 only, GET /callback only, one successful use."""
import asyncio

import pytest

from app import custom_mcp_oauth_loopback as loopback


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    for receiver in list(loopback._open.values()):
        await receiver.close()


async def get(port, target, *, host=None, method="GET", raw=None):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw or f"{method} {target} HTTP/1.1\r\nHost: {host or f'127.0.0.1:{port}'}\r\n\r\n".encode())
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    if not data:
        return None, b""
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split()[1]), body


async def refused(port) -> bool:
    try:
        _, writer = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        return True
    writer.close()
    return False


def completer(status=200, calls=None):
    async def complete(state, code, error):
        if calls is not None:
            calls.append((state, code, error))
        return status, b"<p>done</p>", {"Referrer-Policy": "no-referrer", "Content-Length": "999"}
    return complete


async def test_the_redirect_is_a_loopback_address_and_a_good_sign_in_closes_the_listener():
    calls = []
    receiver = await loopback.open_receiver("c1", completer(200, calls), ttl=30)
    assert receiver.redirect_uri == f"http://127.0.0.1:{receiver.port}/callback"
    status, body = await get(receiver.port, "/callback?state=abc&code=xyz")
    assert status == 200 and body == b"<p>done</p>" and calls == [("abc", "xyz", "")]
    await asyncio.sleep(0.1)
    assert await refused(receiver.port) and "c1" not in loopback._open


async def test_only_get_callback_on_a_loopback_host_is_served():
    calls = []
    receiver = await loopback.open_receiver("c1", completer(200, calls), ttl=30)
    port = receiver.port
    assert (await get(port, "/callback?state=a", host="evil.example.com"))[0] == 400
    assert (await get(port, "/callback?state=a", host="127.0.0.1:1"))[0] == 400
    assert (await get(port, "/other?state=a"))[0] == 404
    assert (await get(port, "/callback", method="POST"))[0] == 405
    assert (await get(port, "", raw=b"garbage\r\n\r\n"))[0] == 400
    assert calls == []
    assert (await get(port, "/callback?state=a", host=f"localhost:{port}"))[0] == 200


async def test_a_refused_sign_in_leaves_the_listener_open_so_noise_cannot_stop_a_real_one():
    receiver = await loopback.open_receiver("c1", completer(400), ttl=30)
    for _ in range(3):
        assert (await get(receiver.port, "/callback?state=guess"))[0] == 400
    assert not await refused(receiver.port)


async def test_an_oversized_or_silent_request_is_dropped_without_stopping_the_listener():
    receiver = await loopback.open_receiver("c1", completer(400), ttl=30)
    big = b"GET /callback HTTP/1.1\r\nHost: 127.0.0.1\r\n" + b"X: " + b"a" * 40_000 + b"\r\n\r\n"
    assert (await get(receiver.port, "", raw=big))[0] is None
    assert not await refused(receiver.port)


async def test_the_listener_closes_when_its_time_is_up():
    receiver = await loopback.open_receiver("c1", completer(400), ttl=0.2)
    await asyncio.sleep(0.6)
    assert await refused(receiver.port) and not loopback._open


async def test_a_newer_start_replaces_the_older_listener_and_the_number_open_is_capped():
    first = await loopback.open_receiver("c1", completer(), ttl=30)
    second = await loopback.open_receiver("c1", completer(), ttl=30)
    assert await refused(first.port) and not await refused(second.port)
    others = [await loopback.open_receiver(f"x{n}", completer(), ttl=30) for n in range(loopback.MAX_OPEN + 2)]
    assert len(loopback._open) <= loopback.MAX_OPEN
    assert await refused(second.port)
    assert not await refused(others[-1].port)


async def test_long_state_values_are_cut_before_they_reach_the_sign_in_code():
    calls = []
    receiver = await loopback.open_receiver("c1", completer(400, calls), ttl=30)
    await get(receiver.port, "/callback?state=" + "s" * 5000 + "&code=" + "c" * 5000)
    assert len(calls[0][0]) == 4096 and len(calls[0][1]) == 4096
