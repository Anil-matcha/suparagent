"""Owner-managed first-party MuAPI API key and agent tool grants."""

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.db.models import Agent, MuapiToolGrant, UserCredential
from app.db.session import get_db
from app.db.transactions import begin_write, release_snapshot
from app.routers.integrations import _owner
from app.services import muapi_data_api, user_credential_service
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import LOCAL_OWNER_ID

router = APIRouter(
    prefix="/integrations/muapi",
    tags=["MuAPI"],
    dependencies=[Depends(require_local_access), Depends(_owner)],
)

_CREDENTIAL_PROVIDER = "integration:muapi:data-api"
_NO_FILE = "-"


class MuapiCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = Field(min_length=1, max_length=4096)


class MuapiGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: list[str] = Field(max_length=len(muapi_data_api.TOOL_DEFINITIONS))


def _schema_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _api_key(db: AsyncSession) -> str | None:
    try:
        found = await user_credential_service.get_decrypted(db, LOCAL_OWNER_ID, _CREDENTIAL_PROVIDER)
        if found is None:
            return None
        value = json.loads(found[0])
        return value if isinstance(value, str) and value else None
    except Exception:  # noqa: BLE001 - a damaged encrypted credential is unavailable
        return None
    finally:
        await release_snapshot(db)


async def _owned_agent(db: AsyncSession, agent_id: int, *, lock: bool = False) -> Agent:
    query = select(Agent).where(
        Agent.id == agent_id, Agent.owner_id == LOCAL_OWNER_ID, Agent.archived_at.is_(None)
    )
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query)
    if row is None:
        raise HTTPException(404, detail={"error_code": "agent_not_found"})
    return row


@router.get("")
async def status(response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return {"configured": await _api_key(db) is not None}


@router.put("")
async def save_key(payload: MuapiCredentialRequest, response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    key = payload.api_key.get_secret_value().strip()
    if not key or len(key.encode("utf-8")) > 4096:
        raise HTTPException(422, detail={"error_code": "invalid_credential"})
    try:
        await muapi_data_api.MuapiDataApi(key).validate_key()
    except muapi_data_api.MuapiError as exc:
        raise HTTPException(exc.status, detail={"error_code": exc.code, "message": str(exc)}) from None
    async with muapi_data_api.connection_lock():
        await user_credential_service.save_token(
            db, user_id=LOCAL_OWNER_ID, provider=_CREDENTIAL_PROVIDER, file_path=_NO_FILE,
            plaintext=json.dumps(key),
        )
    return {"configured": True}


@router.delete("", status_code=204)
async def remove_key(response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    async with muapi_data_api.connection_lock():
        await begin_write(db)
        await db.execute(delete(UserCredential).where(
            UserCredential.user_id == LOCAL_OWNER_ID, UserCredential.provider == _CREDENTIAL_PROVIDER,
        ))
        await db.execute(delete(MuapiToolGrant).where(MuapiToolGrant.agent_id.in_(
            select(Agent.id).where(Agent.owner_id == LOCAL_OWNER_ID)
        )))
        await db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/agents/{agent_id}/grants")
async def list_grants(agent_id: int, response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    await _owned_agent(db, agent_id)
    names = (await db.scalars(select(MuapiToolGrant.tool_name).where(
        MuapiToolGrant.agent_id == agent_id,
    ).order_by(MuapiToolGrant.tool_name))).all()
    return {"tool_names": list(names)}


@router.put("/agents/{agent_id}/grants")
async def save_grants(agent_id: int, payload: MuapiGrantRequest, response: Response,
                      db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    selected = payload.tools
    if len(selected) != len(set(selected)) or any(name not in muapi_data_api.TOOL_BY_NAME for name in selected):
        raise HTTPException(422, detail={"error_code": "invalid_tool_grant"})
    await _owned_agent(db, agent_id)
    if selected and await _api_key(db) is None:
        raise HTTPException(409, detail={"error_code": "credential_required"})
    async with muapi_data_api.agent_grant_lock(agent_id):
        async with muapi_data_api.connection_lock():
            await begin_write(db)
            await _owned_agent(db, agent_id, lock=True)
            key_exists = await db.scalar(select(UserCredential.provider).where(
                UserCredential.user_id == LOCAL_OWNER_ID,
                UserCredential.provider == _CREDENTIAL_PROVIDER,
            ))
            if selected and key_exists is None:
                await db.rollback()
                raise HTTPException(409, detail={"error_code": "credential_required"})
            await db.execute(delete(MuapiToolGrant).where(MuapiToolGrant.agent_id == agent_id))
            for name in selected:
                definition = muapi_data_api.TOOL_BY_NAME[name]
                db.add(MuapiToolGrant(
                    agent_id=agent_id, tool_name=name,
                    schema_digest=_schema_digest(definition["inputSchema"]),
                    description=definition["description"], input_schema=definition["inputSchema"],
                ))
            await db.commit()
    return {"tool_names": selected}
