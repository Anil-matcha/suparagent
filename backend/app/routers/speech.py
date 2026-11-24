"""Spoken replies (BL-53). Owner (admin) only: these routes are not on the chat or files allowlists in
app/auth/policy.py, so a paired device cannot call them. The audio is made with the MuAPI key the owner
saved under Settings, Integrations; the key never leaves the server."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.routers.agents import get_runtime
from app.routers.muapi import _api_key as saved_muapi_key
from app.services import speech
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(tags=["Speech"], dependencies=[Depends(require_local_access)])

EXPOSED = "X-Speech-Cost-Usd, X-Speech-Truncated, X-Speech-Chars"


class SpeakRequest(BaseModel):
    text: str = Field(max_length=speech.TEXT_MAX_CHARS)
    voice: str | None = Field(default=None, max_length=40)


async def _muapi_key(rt: ThreadRuntime) -> str | None:
    async with rt.session() as db:
        return await saved_muapi_key(db)


@router.get("/speech/voices")
async def list_voices(rt: ThreadRuntime = Depends(get_runtime)):
    return speech.voices(bool(await _muapi_key(rt)))


@router.post("/speech/speak")
async def speak(payload: SpeakRequest, rt: ThreadRuntime = Depends(get_runtime)):
    try:
        spoken = await speech.speak(payload.text, payload.voice, await _muapi_key(rt))
    except speech.SpeechError as error:
        raise HTTPException(status_code=error.status,
                            detail={"error_code": error.code, "message": error.message}) from None
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Speech-Truncated": "1" if spoken.truncated else "0",
        "X-Speech-Chars": str(spoken.chars),
        "Access-Control-Expose-Headers": EXPOSED,
    }
    if spoken.cost_usd is not None:
        headers["X-Speech-Cost-Usd"] = f"{spoken.cost_usd:.4f}"
    return Response(spoken.audio, media_type=spoken.mime, headers=headers)
