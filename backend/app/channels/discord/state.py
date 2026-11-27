"""Encrypted Discord connection and its one conversation."""

import json
from dataclasses import asdict, dataclass, fields

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user_credential import UserCredential
from app.db.transactions import begin_write
from app.services import user_credential_service

PROVIDER = "integration:discord"


@dataclass
class DiscordConfig:
    bot_token: str
    bot_user_id: str
    bot_name: str
    owner_user_id: str | None = None
    dm_channel: str | None = None
    agent_id: int | None = None
    thread_id: int | None = None

    @property
    def linked(self) -> bool:
        return bool(self.owner_user_id and self.dm_channel)


async def load(db: AsyncSession, owner_id: str) -> DiscordConfig | None:
    try:
        found = await user_credential_service.get_decrypted(db, owner_id, PROVIDER)
        value = json.loads(found[0]) if found else None
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(value, dict):
        return None
    names = {field.name for field in fields(DiscordConfig)}
    try:
        return DiscordConfig(**{key: val for key, val in value.items() if key in names})
    except TypeError:
        return None


async def save(db: AsyncSession, owner_id: str, config: DiscordConfig) -> None:
    await user_credential_service.save_token(
        db, user_id=owner_id, provider=PROVIDER, file_path="-",
        plaintext=json.dumps(asdict(config), ensure_ascii=False),
    )


async def remove(db: AsyncSession, owner_id: str) -> None:
    await begin_write(db)
    await db.execute(delete(UserCredential).where(UserCredential.user_id == owner_id, UserCredential.provider == PROVIDER))
    await db.commit()
