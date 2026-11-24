"""Which named connections this workspace can use right now.

This module checks local credential state only. It never makes a provider
network call. MuAPI's data API key is separate from the cloud-computer key.
"""

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.transactions import release_snapshot
from app.services import user_credential_service
from app.services.thread_runtime import LOCAL_OWNER_ID

async def _has_muapi_key(db: AsyncSession) -> bool:
    try:
        found = await user_credential_service.get_decrypted(db, LOCAL_OWNER_ID, "integration:muapi:data-api")
        return found is not None and isinstance(json.loads(found[0]), str) and bool(json.loads(found[0]))
    except Exception:  # noqa: BLE001 - an undecryptable secret is treated as absent
        return False
    finally:
        await release_snapshot(db)


async def muapi_ready(db: AsyncSession) -> bool:
    return await _has_muapi_key(db)


async def ready_connections(db: AsyncSession) -> frozenset[str]:
    """The template connection names that are ready in this workspace."""
    ready: set[str] = set()
    if await muapi_ready(db):
        ready.add("muapi")
    return frozenset(ready)
