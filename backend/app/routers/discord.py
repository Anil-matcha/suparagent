"""Owner-managed Discord DM channel. The bot token is write-only."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.channels.discord.service import DiscordService, DiscordSetupError, get_service
from app.routers.integrations import _owner
from app.services.local_access_policy import require_local_access

router = APIRouter(
    prefix="/integrations/discord",
    tags=["Discord"],
    dependencies=[Depends(require_local_access), Depends(_owner)],
)
MAX_TOKEN_BODY = 4_096


class DiscordAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: int = Field(ge=1)


def get_discord_service() -> DiscordService:
    return get_service()


def _fail(exc: DiscordSetupError) -> HTTPException:
    return HTTPException(exc.status, detail={"error_code": exc.code, "message": exc.message})


@router.get("")
async def discord_status(response: Response, service: DiscordService = Depends(get_discord_service)):
    response.headers["Cache-Control"] = "no-store"
    return await service.status()


@router.put("/credentials")
async def connect_discord(request: Request, response: Response, service: DiscordService = Depends(get_discord_service)):
    # FastAPI validation errors can repeat rejected input, so parse token bodies
    # here and return only fixed error text.
    response.headers["Cache-Control"] = "no-store"
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_TOKEN_BODY:
            break
    token = None
    if len(body) <= MAX_TOKEN_BODY:
        try:
            payload = json.loads(bytes(body))
        except ValueError:
            payload = None
        if isinstance(payload, dict) and set(payload) == {"bot_token"} and isinstance(payload["bot_token"], str):
            token = payload["bot_token"]
    if token is None:
        raise HTTPException(422, detail={"error_code": "invalid_discord_token", "message": "Send a bot token."})
    try:
        return await service.connect(token)
    except DiscordSetupError as exc:
        raise _fail(exc) from None


@router.post("/link-code")
async def make_link_code(response: Response, service: DiscordService = Depends(get_discord_service)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await service.issue_link_code()
    except DiscordSetupError as exc:
        raise _fail(exc) from None


@router.put("/agent")
async def choose_agent(payload: DiscordAgent, response: Response, service: DiscordService = Depends(get_discord_service)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await service.set_agent(payload.agent_id)
    except DiscordSetupError as exc:
        raise _fail(exc) from None


@router.delete("", status_code=204)
async def disconnect_discord(service: DiscordService = Depends(get_discord_service)):
    await service.disconnect()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
