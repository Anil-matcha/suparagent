"""Image generation and agent avatars (BL-114). Owner (admin) only: these routes are not on
the chat or files allowlists in app/auth/policy.py, so a paired device cannot call them."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.routers.agents import _agent_or_404, get_runtime
from app.routers.muapi import _api_key as saved_muapi_key
from app.services import image_gen
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(tags=["Images"], dependencies=[Depends(require_local_access)])


class ProviderOut(BaseModel):
    id: str
    label: str
    available: bool


class GenerateRequest(BaseModel):
    prompt: str = Field(max_length=4000)
    provider: str | None = Field(default=None, max_length=40)
    size: str | None = Field(default=None, max_length=20)


class ImageOut(BaseModel):
    id: str
    mime: str
    bytes: int
    provider: str


class AvatarRequest(BaseModel):
    image_id: str = Field(max_length=64)


def _refuse(error: image_gen.ImageError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error_code": error.code, "message": error.message})


async def _muapi_key(rt: ThreadRuntime) -> str | None:
    """The MuAPI key the owner saved under Settings, Integrations; the pictures provider is set up when there is one."""
    async with rt.session() as db:
        return await saved_muapi_key(db)


@router.get("/images/providers", response_model=list[ProviderOut])
async def list_providers(rt: ThreadRuntime = Depends(get_runtime)):
    return image_gen.providers(await _muapi_key(rt))


@router.post("/images/generate", response_model=ImageOut, status_code=201)
async def generate_image(payload: GenerateRequest, rt: ThreadRuntime = Depends(get_runtime)):
    try:
        return await image_gen.generate(payload.prompt, payload.provider, payload.size, await _muapi_key(rt))
    except image_gen.ImageError as error:
        raise _refuse(error) from None


@router.get("/images/{image_id}")
async def get_image(image_id: str):
    try:
        data, mime = await asyncio.to_thread(image_gen.read_generated, image_id)
    except image_gen.ImageError as error:
        raise _refuse(error) from None
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.put("/agents/{agent_id}/avatar", status_code=204)
async def set_avatar(agent_id: int, payload: AvatarRequest, rt: ThreadRuntime = Depends(get_runtime)):
    await _agent_or_404(rt, agent_id)
    try:
        await asyncio.to_thread(image_gen.set_avatar, agent_id, payload.image_id)
    except image_gen.ImageError as error:
        raise _refuse(error) from None
    return Response(status_code=204)


@router.get("/agents/{agent_id}/avatar")
async def get_avatar(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    await _agent_or_404(rt, agent_id)
    found = await asyncio.to_thread(image_gen.read_avatar, agent_id)
    if found is None:
        raise HTTPException(status_code=404, detail={"error_code": "no_avatar", "message": "This agent has no picture."})
    return Response(found[0], media_type=found[1], headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


@router.delete("/agents/{agent_id}/avatar", status_code=204)
async def delete_avatar(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    await _agent_or_404(rt, agent_id)
    await asyncio.to_thread(image_gen.remove_avatar, agent_id)
    return Response(status_code=204)
