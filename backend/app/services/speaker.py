"""Who is talking to an agent, once several people share the workspace.

The engine only ever sees the text of a message, so without this two people chatting with the same agent
look like one. With more than one person in the workspace, a message from a signed-in member is
introduced by name. A workspace with only its owner sends exactly what was typed, as before.
The name is the person's own text, so it goes in as a quoted, bracket-free string (team/fence.py).
"""

import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.people import Person
from app.team import fence


def note(label: str | None, people_count: int) -> str:
    """The introduction to put before a member's message, or "" when it would add nothing."""
    name = fence.line(label or "")
    if not name or people_count < 2:
        return ""
    return f"(This message is from {json.dumps(name)}. Other people share this workspace and talk to you too.)\n\n"


async def count_people(db: AsyncSession) -> int:
    result = await db.execute(select(func.count()).select_from(Person).where(Person.status == "active"))
    return int(result.scalar_one())


def learns_from(person_id: int | None, is_owner: bool) -> bool:
    """Whether agent memory may learn from this chat as if the owner had said it. A message with no
    signed-in person behind it keeps today's behavior; another person's is not the owner's words."""
    return person_id is None or is_owner
