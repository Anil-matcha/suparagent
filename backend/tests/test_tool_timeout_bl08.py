import asyncio

import pytest

from app.core import tool_timeout
from app.core.config import Settings
from app.services import composio_service
from app.services.composio_client import ComposioError


class _Slow:
    async def execute(self, *args):
        await asyncio.sleep(10)


class _Fast:
    async def execute(self, *args):
        return "ok"


def test_default_and_bounds(monkeypatch):
    assert Settings().TOOL_CALL_TIMEOUT_SECONDS == 120
    for bad in (1, 4, 1801):
        with pytest.raises(ValueError):
            Settings(TOOL_CALL_TIMEOUT_SECONDS=bad)
    assert Settings(TOOL_CALL_TIMEOUT_SECONDS=5).TOOL_CALL_TIMEOUT_SECONDS == 5


def test_stuck_call_is_stopped(monkeypatch):
    monkeypatch.setattr(tool_timeout, "seconds", lambda: 0.05)
    with pytest.raises(ComposioError) as caught:
        asyncio.run(composio_service._execute(_Slow(), "s", "SLUG", {}, "a"))
    assert caught.value.kind == "unavailable"  # a write is then reported as outcome unknown


def test_fast_call_passes(monkeypatch):
    monkeypatch.setattr(tool_timeout, "seconds", lambda: 5)
    assert asyncio.run(composio_service._execute(_Fast(), "s", "SLUG", {}, "a")) == "ok"
