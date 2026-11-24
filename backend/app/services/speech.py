"""Spoken replies (BL-53): turn the text of a finished reply into audio through MuAPI.

The owner asks for one reply to be read aloud; nothing is spoken on its own. The text is first made
fit for the ear (Markdown removed, code left out, anything that looks like a credential replaced), cut
at a sentence boundary when it is longer than the voice allows, then sent to MuAPI with the key the
owner saved under Settings, Integrations. The audio comes back as bytes for the browser to play and is
not stored. Provider error text is never passed on.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from app.core import tool_timeout
from app.services import media_fetch
from app.services.agent_memory import redact_secrets
from app.services.muapi_data_api import MuapiDataApi, MuapiError, _model_endpoint

log = logging.getLogger(__name__)

# MuAPI links the finished audio on its CDN.
AUDIO_URL_HOSTS = frozenset({"cdn.muapi.ai"})
AUDIO_MAX_BYTES = 10 * 1024 * 1024
TEXT_MAX_CHARS = 20_000  # what the route accepts; the voice's own limit is applied after cleaning
POLL_SECONDS = 1.5
WAIT_SECONDS = 75.0
# A guard against a loop or a stuck button spending the owner's money: this many spoken replies
# in the window, per server process.
RATE_MAX = 20
RATE_WINDOW_SECONDS = 600


class SpeechError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    model: str
    voice_id: str
    max_chars: int


# One choice of voice is one MuAPI task plus the voice it takes. Prices are MuAPI's and change; the
# owner sees the quoted cost after each reply is spoken.
VOICES: dict[str, Voice] = {voice.id: voice for voice in (
    Voice("eleven-daniel", "Daniel (ElevenLabs)", "elevenlabs-v4-turbo", "voice_daniel", 5000),
    Voice("eleven-liam", "Liam (ElevenLabs)", "elevenlabs-v4-turbo", "voice_liam", 5000),
    Voice("fish-narrator", "Narrator (Fish Audio)", "fish-s2-1-pro", "voice_fish_narrator", 10000),
)}
DEFAULT_VOICE = "eleven-daniel"


@dataclass(frozen=True)
class Spoken:
    audio: bytes
    mime: str
    cost_usd: float | None
    truncated: bool
    chars: int


# --- making text fit for the ear ---------------------------------------------------------------

_FENCE = re.compile(r"(```|~~~)[^\n]*\n.*?(\1|\Z)", re.S)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_TAG = re.compile(r"<[^>\n]{1,200}>")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_QUOTE = re.compile(r"^\s*>+\s?", re.M)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+", re.M)
_RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$", re.M)
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$", re.M)
_EMPHASIS = re.compile(r"(\*\*|__|~~|\*|(?<!\w)_|_(?!\w))")
_END = re.compile(r"[.!?:;。！？]$")
_REDACTED_WORD = "secret omitted"


def normalize_for_speech(markdown: str) -> str:
    """Plain sentences from Markdown. Code is left out, links read as their text, and anything that
    looks like a credential is replaced, so nothing sensitive is read aloud."""
    text, _ = redact_secrets(markdown or "")
    text = text.replace("[redacted]", _REDACTED_WORD)
    text = _FENCE.sub("\n\nCode omitted.\n\n", text)
    text = _IMAGE.sub(lambda m: m.group(1).strip() or "image", text)
    text = _LINK.sub(lambda m: m.group(1), text)
    text = _URL.sub("link", text)
    text = _TAG.sub(" ", text)
    text = _TABLE_RULE.sub("", text)
    text = text.replace("|", ", ")
    text = _RULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _QUOTE.sub("", text)
    text = _BULLET.sub("", text)
    text = text.replace("`", "")
    text = _EMPHASIS.sub("", text)
    paragraphs = []
    for block in re.split(r"\n\s*\n", text):
        lines = [" ".join(line.split()) for line in block.splitlines()]
        lines = [line.strip(" ,") for line in lines if line.strip(" ,")]
        if not lines:
            continue
        joined = " ".join(f"{line}" if _END.search(line) else f"{line}." for line in lines)
        paragraphs.append(joined)
    return " ".join(paragraphs).strip()


def cut_at_boundary(text: str, limit: int) -> tuple[str, bool]:
    """The text, shortened to at most `limit` characters at the last sentence end (or, failing
    that, the last space), and whether anything was cut."""
    if len(text) <= limit:
        return text, False
    window = text[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?。！？](?:\s|$)", window)]
    if ends and ends[-1] >= limit // 3:
        return window[:ends[-1]].strip(), True
    space = window.rfind(" ")
    return (window[:space] if space > limit // 3 else window).strip(), True


# --- the provider ------------------------------------------------------------------------------

_recent: deque[float] = deque()


def _check_rate() -> None:
    now = time.monotonic()
    while _recent and now - _recent[0] > RATE_WINDOW_SECONDS:
        _recent.popleft()
    if len(_recent) >= RATE_MAX:
        raise SpeechError(429, "rate_limited", "That is a lot of spoken replies in a short time. Wait a few minutes and try again.")
    _recent.append(now)


def sniff_audio(data: bytes) -> str | None:
    """The audio type of the bytes, or None when they are not a format a browser plays."""
    if data[:3] == b"ID3" or (len(data) > 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[:4] == b"fLaC":
        return "audio/flac"
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    return None


def find_audio_url(value: Any, depth: int = 0) -> str | None:
    """The first link in a finished task's result: MuAPI puts it under output, outputs or result."""
    if depth > 4:
        return None
    if isinstance(value, str):
        return value if value.startswith("https://") else None
    if isinstance(value, list):
        for item in value:
            found = find_audio_url(item, depth + 1)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("output", "outputs", "audio_url", "url", "audio", "result"):
            if key in value:
                found = find_audio_url(value[key], depth + 1)
                if found:
                    return found
    return None


def voices(available: bool) -> dict:
    return {
        "available": available,
        "default": DEFAULT_VOICE,
        "voices": [{"id": v.id, "label": v.label, "max_chars": v.max_chars} for v in VOICES.values()],
    }


async def speak(text: str, voice: str | None, api_key: str | None) -> Spoken:
    if not (api_key or "").strip():
        raise SpeechError(409, "provider_not_configured",
                          "Spoken replies need MuAPI. Connect it under Settings, Integrations.")
    chosen = VOICES.get(voice or DEFAULT_VOICE)
    if chosen is None:
        raise SpeechError(422, "unknown_voice", "That voice does not exist.")
    plain = normalize_for_speech(text[:TEXT_MAX_CHARS * 2])
    if not plain:
        raise SpeechError(422, "nothing_to_say", "There is nothing in this reply to read aloud.")
    spoken, truncated = cut_at_boundary(plain, chosen.max_chars)
    _check_rate()
    payload = {"prompt": spoken, "voice_id": chosen.voice_id}
    client = MuapiDataApi(api_key.strip())
    try:
        async with asyncio.timeout(WAIT_SECONDS + tool_timeout.seconds()):
            info = await client._request("GET", f"models/{chosen.model}")
            endpoint = _model_endpoint(info if isinstance(info, dict) else {})
            quote = await _quote(client, chosen.model, payload)
            submission = await client._request("POST", endpoint, body=payload, timeout=90.0)
            result = await _wait_for_result(client, submission)
            url = find_audio_url(result.get("output") if "output" in result else result)
            if url is None:
                raise SpeechError(502, "provider_failed", "The voice service did not return any audio.")
            audio = await media_fetch.fetch(url, AUDIO_URL_HOSTS, AUDIO_MAX_BYTES)
    except SpeechError:
        raise
    except MuapiError as error:
        log.warning("speech provider error %s", error.code)
        if error.code == "authentication_failed":
            raise SpeechError(409, "provider_not_configured",
                              "MuAPI did not accept the saved key. Reconnect it under Settings, Integrations.") from None
        if error.status == 402:
            raise SpeechError(402, "insufficient_credit", "MuAPI says the account is out of credit.") from None
        raise SpeechError(502, "provider_failed", "The voice service could not read that aloud.") from None
    except media_fetch.MediaFetchError:
        raise SpeechError(502, "provider_failed", "The voice service could not read that aloud.") from None
    except TimeoutError:
        raise SpeechError(504, "timed_out", "The voice service took too long.") from None
    mime = sniff_audio(audio)
    if mime is None:
        raise SpeechError(502, "not_audio", "The voice service returned something that is not audio.")
    cost = submission.get("cost", {}).get("amount_usd") if isinstance(submission, dict) and isinstance(
        submission.get("cost"), dict) else None
    return Spoken(audio=audio, mime=mime, cost_usd=cost if isinstance(cost, (int, float)) else quote,
                  truncated=truncated, chars=len(spoken))


async def _quote(client: MuapiDataApi, model: str, payload: dict) -> float | None:
    try:
        result = await client._request("POST", f"models/{model}/estimate-cost", body=payload)
    except MuapiError:
        return None
    cost = result.get("cost") if isinstance(result, dict) else None
    return float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None


async def _wait_for_result(client: MuapiDataApi, submission: object) -> dict:
    request_id = submission.get("request_id") or submission.get("id") if isinstance(submission, dict) else None
    if not isinstance(request_id, str) or not request_id:
        raise SpeechError(502, "provider_failed", "The voice service could not read that aloud.")
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        result = await client._request("GET", f"predictions/{request_id}/result", failed_result_ok=True)
        status = result.get("status") if isinstance(result, dict) else None
        if status == "failed":
            log.warning("speech task failed")
            raise SpeechError(502, "provider_failed", "The voice service could not read that aloud.")
        if status in ("completed", "succeeded", "success"):
            return result
        if time.monotonic() > deadline:
            raise SpeechError(504, "timed_out", "The voice service took too long.")
        await asyncio.sleep(POLL_SECONDS)
