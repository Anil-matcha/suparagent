"""Image generation for agent avatars (BL-114).

A small provider interface (`ImageProvider`) with one real adapter for any
OpenAI-compatible images endpoint, configured by `IMAGE_API_BASE_URL`,
`IMAGE_API_KEY` and `IMAGE_MODEL`. Generated images are stored in a private
folder next to the attachments folder, never inside a workspace, under a
random id. The prompt, size and result are bounded; the key is never logged
and provider error text is never passed on.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from app.core import tool_timeout
from app.core.config import settings
from app.services import attachments

log = logging.getLogger(__name__)

PROMPT_MAX_CHARS = 1000
IMAGE_MAX_BYTES = 10 * 1024 * 1024
SIZES = ("1024x1024", "1536x1024", "1024x1536")
DEFAULT_SIZE = SIZES[0]
KEEP_GENERATED = 50
_ID = re.compile(r"^[0-9a-f]{32}$")
_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png", ".png"), (b"\xff\xd8\xff", "image/jpeg", ".jpg"))


class ImageError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class ImageProvider(Protocol):
    id: str
    label: str

    def available(self) -> bool: ...

    async def generate(self, prompt: str, size: str) -> bytes: ...


class OpenAICompatibleProvider:
    id = "openai"
    label = "OpenAI-compatible images API"

    def available(self) -> bool:
        return bool(settings.IMAGE_API_KEY.strip())

    async def generate(self, prompt: str, size: str) -> bytes:
        base = settings.IMAGE_API_BASE_URL.strip().rstrip("/")
        headers = {"Authorization": f"Bearer {settings.IMAGE_API_KEY.strip()}"}
        body = {"model": settings.IMAGE_MODEL, "prompt": prompt, "size": size, "n": 1}
        async with httpx.AsyncClient(timeout=httpx.Timeout(tool_timeout.seconds()), follow_redirects=False) as client:
            response = await client.post(f"{base}/images/generations", json=body, headers=headers)
        if response.status_code != 200:
            raise ImageError(502, "provider_failed", "The image service could not make that image.")
        try:
            data = response.json()["data"][0]["b64_json"]
            return base64.b64decode(data, validate=True)
        except Exception:
            raise ImageError(502, "provider_failed", "The image service returned something that is not an image.") from None


_PROVIDERS: dict[str, ImageProvider] = {"openai": OpenAICompatibleProvider()}


def providers() -> list[dict]:
    return [{"id": p.id, "label": p.label, "available": p.available()} for p in _PROVIDERS.values()]


def sniff(data: bytes) -> tuple[str, str] | None:
    """(mime, extension) for PNG or JPEG bytes, or WebP; None for anything else."""
    for magic, mime, ext in _MAGIC:
        if data.startswith(magic):
            return mime, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def root() -> Path:
    return attachments.root_dir().parent / "generated-images"


def avatars_root() -> Path:
    return attachments.root_dir().parent / "agent-avatars"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


def _trim() -> None:
    files = sorted((p for p in root().glob("*") if p.suffix in (".png", ".jpg", ".webp")), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP_GENERATED:]:
        old.unlink(missing_ok=True)


async def generate(prompt: str, provider: str | None = None, size: str | None = None) -> dict:
    text = " ".join((prompt or "").split())
    if not text:
        raise ImageError(422, "empty_prompt", "Describe the image first.")
    if len(text) > PROMPT_MAX_CHARS:
        raise ImageError(422, "prompt_too_long", f"Keep the description under {PROMPT_MAX_CHARS} characters.")
    size = size or DEFAULT_SIZE
    if size not in SIZES:
        raise ImageError(422, "bad_size", "Pick one of the offered sizes.")
    chosen = _PROVIDERS.get(provider or "openai")
    if chosen is None:
        raise ImageError(422, "unknown_provider", "That image provider does not exist.")
    if not chosen.available():
        raise ImageError(409, "provider_not_configured", f"{chosen.label} is not set up. Add an image API key in the server settings.")
    try:
        data = await asyncio.wait_for(chosen.generate(text, size), timeout=tool_timeout.seconds())
    except asyncio.TimeoutError:
        raise ImageError(504, "timed_out", "The image service took too long.") from None
    except ImageError:
        raise
    except Exception:
        log.warning("image provider %s failed", chosen.id)
        raise ImageError(502, "provider_failed", "The image service could not make that image.") from None
    if len(data) > IMAGE_MAX_BYTES:
        raise ImageError(502, "too_large", "The image came back too large.")
    kind = sniff(data)
    if kind is None:
        raise ImageError(502, "not_an_image", "The image service returned something that is not an image.")
    image_id = uuid.uuid4().hex
    await asyncio.to_thread(_write, root() / f"{image_id}{kind[1]}", data)
    await asyncio.to_thread(_trim)
    return {"id": image_id, "mime": kind[0], "bytes": len(data), "provider": chosen.id}


def _find(directory: Path, name: str) -> Path | None:
    for ext in (".png", ".jpg", ".webp"):
        path = directory / f"{name}{ext}"
        if path.is_file():
            return path
    return None


def read_generated(image_id: str) -> tuple[bytes, str]:
    if not _ID.match(image_id or ""):
        raise ImageError(404, "not_found", "That image is gone.")
    path = _find(root(), image_id)
    if path is None:
        raise ImageError(404, "not_found", "That image is gone.")
    data = path.read_bytes()
    return data, sniff(data)[0]  # type: ignore[index]


def set_avatar(agent_id: int, image_id: str) -> None:
    data, _ = read_generated(image_id)
    remove_avatar(agent_id)
    ext = sniff(data)[1]  # type: ignore[index]
    _write(avatars_root() / f"{int(agent_id)}{ext}", data)


def read_avatar(agent_id: int) -> tuple[bytes, str] | None:
    path = _find(avatars_root(), str(int(agent_id)))
    if path is None:
        return None
    data = path.read_bytes()
    return data, sniff(data)[0]  # type: ignore[index]


def remove_avatar(agent_id: int) -> None:
    for ext in (".png", ".jpg", ".webp"):
        (avatars_root() / f"{int(agent_id)}{ext}").unlink(missing_ok=True)
