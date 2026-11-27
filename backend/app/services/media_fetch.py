"""Fetch a media file a provider linked to, and nothing else.

A provider answers with a link to the file it made. This follows that link only over HTTPS to a fixed
list of hosts, never sends credentials, never follows a redirect, and stops reading at a size limit,
so a provider cannot point the server at an internal address or hand back an endless body.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from app.core import tool_timeout


class MediaFetchError(Exception):
    pass


def allowed_url(url: str, hosts: frozenset[str]) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in hosts and parts.username is None
                and parts.port in (None, 443))
    except ValueError:
        return False


async def fetch(url: str, hosts: frozenset[str], max_bytes: int) -> bytes:
    if not allowed_url(url, hosts):
        raise MediaFetchError("The link is not on an allowed host.")
    pieces: list[bytes] = []
    size = 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(tool_timeout.seconds()), follow_redirects=False,
                                     trust_env=False) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise MediaFetchError("The file could not be fetched.")
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise MediaFetchError("The file is too large.")
                    pieces.append(chunk)
    except MediaFetchError:
        raise
    except httpx.HTTPError:
        raise MediaFetchError("The file could not be fetched.") from None
    return b"".join(pieces)
