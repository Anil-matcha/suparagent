"""Owner-managed Slack channel: connect with a bot token and an app-level token,
link the owner's Slack account with a one-time code, choose the agent, and
disconnect. The tokens are write-only: nothing here returns one."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.channels.slack.service import SlackService, SlackSetupError, get_service
from app.routers.integrations import _owner
from app.services.local_access_policy import require_local_access

router = APIRouter(
    prefix="/integrations/slack",
    tags=["Slack"],
    dependencies=[Depends(require_local_access), Depends(_owner)],
)


# Two tokens are a few hundred bytes; anything near this is not one.
MAX_TOKEN_BODY = 8_192


class SlackAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: int = Field(ge=1)


def get_slack_service() -> SlackService:
    return get_service()


def _fail(exc: SlackSetupError) -> HTTPException:
    return HTTPException(exc.status, detail={"error_code": exc.code, "message": exc.message})


@router.get("")
async def slack_status(response: Response, service: SlackService = Depends(get_slack_service)):
    response.headers["Cache-Control"] = "no-store"
    return await service.status()


@router.put("/credentials")
async def connect_slack(request: Request, response: Response, service: SlackService = Depends(get_slack_service)):
    """Check both tokens with Slack and save them encrypted. The body is read
    by hand, not through a model: FastAPI's own validation error repeats the
    rejected input, which here would be a token."""
    response.headers["Cache-Control"] = "no-store"
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_TOKEN_BODY:
            break
    tokens = None
    if len(body) <= MAX_TOKEN_BODY:
        try:
            payload = json.loads(bytes(body))
        except ValueError:
            payload = None
        if (isinstance(payload, dict) and set(payload) == {"bot_token", "app_token"}
                and all(isinstance(payload[key], str) for key in payload)):
            tokens = (payload["bot_token"], payload["app_token"])
    if tokens is None:
        raise HTTPException(422, detail={"error_code": "invalid_slack_tokens", "message": "Send the bot token and the app-level token."})
    try:
        return await service.connect(*tokens)
    except SlackSetupError as exc:
        raise _fail(exc) from None


@router.post("/link-code")
async def make_link_code(response: Response, service: SlackService = Depends(get_slack_service)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await service.issue_link_code()
    except SlackSetupError as exc:
        raise _fail(exc) from None


@router.put("/agent")
async def choose_agent(payload: SlackAgent, response: Response, service: SlackService = Depends(get_slack_service)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await service.set_agent(payload.agent_id)
    except SlackSetupError as exc:
        raise _fail(exc) from None


@router.delete("", status_code=204)
async def disconnect_slack(service: SlackService = Depends(get_slack_service)):
    await service.disconnect()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
