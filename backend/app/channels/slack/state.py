"""The saved Slack connection: one encrypted credential row.

Provider `integration:slack` in the existing credential table (the same
encryption as every other credential, no migration). The JSON holds both
tokens, who the app is, who the owner is once linked, and the one conversation
the DM is attached to. It is read fresh whenever it is needed, so a change made
in Settings applies without a restart, and it never leaves the backend: the
status the API returns is built from `SlackConfig.public()`, which has no token.
"""

import json
from dataclasses import asdict, dataclass, fields

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user_credential import UserCredential
from app.db.transactions import begin_write
from app.services import user_credential_service

PROVIDER = "integration:slack"
_NOT_A_FILE = "-"


@dataclass
class SlackConfig:
    bot_token: str
    app_token: str
    team_id: str
    team_name: str
    bot_user_id: str
    bot_id: str | None = None
    owner_user_id: str | None = None
    dm_channel: str | None = None
    agent_id: int | None = None
    thread_id: int | None = None

    @property
    def linked(self) -> bool:
        return bool(self.owner_user_id and self.dm_channel)

    def public(self) -> dict:
        return {"team": self.team_name, "linked": self.linked, "agent_id": self.agent_id}


async def load(db: AsyncSession, owner_id: str) -> SlackConfig | None:
    """The saved connection, or None when there is none or it cannot be read."""
    try:
        found = await user_credential_service.get_decrypted(db, owner_id, PROVIDER)
        value = json.loads(found[0]) if found else None
    except Exception:  # noqa: BLE001  (a damaged row reads as not connected)
        return None
    if not isinstance(value, dict):
        return None
    names = {f.name for f in fields(SlackConfig)}
    try:
        return SlackConfig(**{k: v for k, v in value.items() if k in names})
    except TypeError:
        return None


async def save(db: AsyncSession, owner_id: str, config: SlackConfig) -> None:
    await user_credential_service.save_token(
        db, user_id=owner_id, provider=PROVIDER, file_path=_NOT_A_FILE,
        plaintext=json.dumps(asdict(config), ensure_ascii=False),
    )


async def remove(db: AsyncSession, owner_id: str) -> None:
    await begin_write(db)
    await db.execute(delete(UserCredential).where(UserCredential.user_id == owner_id, UserCredential.provider == PROVIDER))
    await db.commit()
