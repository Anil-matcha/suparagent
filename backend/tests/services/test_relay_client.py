from __future__ import annotations

import json

import httpx
import pytest

from app.services.relay_client import RelayClient, RelayError, RelaySession

BASE = "https://suparagent.ai/api/v1/accounts/relay"
ROUTE = "A" * 24


@pytest.mark.asyncio
async def test_register_accepts_only_exact_https_relay_route():
    requested = []
    public_url = f"{BASE}/hook/{ROUTE}"

    async def handle(request):
        requested.append(request)
        return httpx.Response(201, json={"route_id": ROUTE, "public_url": public_url})

    client = RelayClient(lambda: RelaySession(BASE, "private-token"), httpx.MockTransport(handle))
    assert await client.register_route("s" * 32) == (ROUTE, public_url)
    assert requested[0].url == f"{BASE}/routes"
    assert requested[0].headers["authorization"] == "Bearer private-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", [
    "http://evil.example/api/v1/accounts/relay",
    "https://evil.example/api/v1/accounts/relay",
    "https://user@suparagent.ai/api/v1/accounts/relay",
    "https://suparagent.ai/api/v1/accounts/relay?next=https://evil.example",
    "https://suparagent.ai/api/v1/accounts/relay#fragment",
    "https://suparagent.ai:bad/api/v1/accounts/relay",
    "https://suparagent.ai/api/v1/accounts/other",
])
async def test_invalid_base_url_is_rejected_before_bearer_request(base_url):
    requested = []

    async def handle(request):
        requested.append(request)
        return httpx.Response(204)

    client = RelayClient(lambda: RelaySession(base_url, "private-token"), httpx.MockTransport(handle))
    with pytest.raises(RelayError):
        await client.delete_route(ROUTE)
    assert requested == []


@pytest.mark.asyncio
@pytest.mark.parametrize("public_url", [
    f"http://suparagent.ai/api/v1/accounts/relay/hook/{ROUTE}",
    f"https://evil.example/api/v1/accounts/relay/hook/{ROUTE}",
    f"https://attacker@suparagent.ai/api/v1/accounts/relay/hook/{ROUTE}",
    f"https://suparagent.ai/api/v1/accounts/relay/hook/{ROUTE}?next=https://evil.example",
    f"https://suparagent.ai/api/v1/accounts/relay/hook/{ROUTE}#fragment",
    f"https://suparagent.ai/api/v1/accounts/relay/hook/{'B' * 24}",
    f"https://suparagent.ai/api/v1/accounts/other/{ROUTE}",
])
async def test_register_rejects_hostile_or_mismatched_public_url(public_url):
    async def handle(_request):
        return httpx.Response(201, json={"route_id": ROUTE, "public_url": public_url})

    client = RelayClient(lambda: RelaySession(BASE, "private-token"), httpx.MockTransport(handle))
    with pytest.raises(RelayError):
        await client.register_route("secret")


@pytest.mark.asyncio
async def test_loopback_http_base_is_limited_to_local_development():
    base = "http://127.0.0.1:8765/api/v1/accounts/relay"
    requested = []

    async def handle(request):
        requested.append(request)
        return httpx.Response(201, json={"route_id": ROUTE,
                                         "public_url": f"https://127.0.0.1:8765/api/v1/accounts/relay/hook/{ROUTE}"})

    client = RelayClient(lambda: RelaySession(base, "private-token"), httpx.MockTransport(handle))
    await client.register_route("s" * 32)
    assert len(requested) == 1


@pytest.mark.asyncio
async def test_staging_account_service_is_an_allowed_relay_host():
    base = "https://staging.suparagent.ai/api/v1/accounts/relay"
    public_url = f"{base}/hook/{ROUTE}"

    async def handle(_request):
        return httpx.Response(201, json={"route_id": ROUTE, "public_url": public_url})

    client = RelayClient(lambda: RelaySession(base, "private-token"), httpx.MockTransport(handle))
    assert await client.register_route("s" * 32) == (ROUTE, public_url)
