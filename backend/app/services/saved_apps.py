"""Saved apps: a page kept past the chat that showed it.

A saved app snapshots the `page.shown` events of one page of one thread (newest 20 revisions), and keeps
following that page: when update_page records a new revision, `append_version` adds it. Nothing here touches a
thread's rows, so deleting the chat leaves the app readable."""
import re
import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import Agent, Event, SavedApp, Thread
from app.db.transactions import begin_write

EVENT = "page.shown"
VERSIONS_MAX = 20
APPS_MAX = 200
NAME_MAX = 80
HTML_MAX_BYTES = 262144
ID_RE = re.compile(r"^sa_[A-Za-z0-9_-]{16}$")
PAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FIELDS = ("title", "html", "height", "frameless", "uses")


class SavedAppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def new_id() -> str:
    while True:
        candidate = "sa_" + secrets.token_urlsafe(12)[:16]
        if ID_RE.match(candidate):
            return candidate


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _version(data: dict, at: str | None = None) -> dict | None:
    """One stored version from a page event's data, or None when it is not a usable page."""
    rev, html = data.get("rev"), data.get("html")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 1:
        return None
    if not isinstance(html, str) or not html.strip() or len(html.encode("utf-8")) > HTML_MAX_BYTES:
        return None
    return {
        "rev": rev, "title": str(data.get("title") or "")[:NAME_MAX], "html": html,
        "height": data.get("height"), "frameless": bool(data.get("frameless", False)),
        "uses": list(data.get("uses") or []), "at": at or _now(),
    }


def trim(versions: list[dict], pinned_rev: int | None) -> list[dict]:
    """Oldest first by rev, newest 20 kept, the pinned one always kept."""
    ordered = sorted(versions, key=lambda v: v["rev"])
    keep = {v["rev"] for v in ordered[-VERSIONS_MAX:]}
    if pinned_rev is not None:
        keep.add(pinned_rev)
    return [v for v in ordered if v["rev"] in keep]


def summary(app: SavedApp, *, chat: bool, agent_name: str | None) -> dict:
    versions = app.versions or []
    return {
        "id": app.id, "name": app.name, "agent_id": app.agent_id, "agent_name": agent_name,
        "thread_id": app.thread_id, "page_id": app.page_id,
        "rev": max((v["rev"] for v in versions), default=0), "pinned_rev": app.pinned_rev,
        "versions": [{"rev": v["rev"], "at": v["at"]} for v in versions], "uses": app.uses or [],
        "created_at": app.created_at, "updated_at": app.updated_at, "chat": chat,
    }


async def summaries(db, owner_id: str, apps: list[SavedApp]) -> list[dict]:
    thread_ids = {a.thread_id for a in apps if a.thread_id is not None}
    agent_ids = {a.agent_id for a in apps if a.agent_id is not None}
    live = set()
    if thread_ids:
        live = set((await db.execute(
            select(Thread.id).where(Thread.id.in_(thread_ids), Thread.owner_id == owner_id))).scalars())
    names = {}
    if agent_ids:
        names = dict((await db.execute(
            select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids), Agent.owner_id == owner_id))).all())
    return [summary(a, chat=a.thread_id in live, agent_name=names.get(a.agent_id)) for a in apps]


async def list_apps(db, owner_id: str) -> list[SavedApp]:
    query = select(SavedApp).where(SavedApp.owner_id == owner_id).order_by(SavedApp.updated_at.desc(), SavedApp.id)
    return list((await db.execute(query)).scalars())


async def get_app(db, owner_id: str, app_id: str) -> SavedApp | None:
    if not ID_RE.match(app_id):
        return None
    app = await db.get(SavedApp, app_id)
    return app if app is not None and app.owner_id == owner_id else None


async def create(db, owner_id: str, thread_id: int, page_id: str) -> tuple[SavedApp, bool]:
    """Save a page of a thread. Returns (app, created); saving the same page again returns the existing app."""
    await begin_write(db)
    thread = await db.get(Thread, thread_id)
    if thread is None or thread.owner_id != owner_id:
        await db.rollback()
        raise SavedAppError(404, "not_found", "No such conversation.")
    if thread.room_id is not None:
        await db.rollback()
        raise SavedAppError(409, "room_thread", "Pages in a room cannot be saved as apps.")
    existing = (await db.execute(
        select(SavedApp).where(SavedApp.owner_id == owner_id, SavedApp.page_id == page_id))).scalar_one_or_none()
    if existing is not None:
        await db.commit()  # ends the write transaction; commit keeps the loaded row readable
        return existing, False
    rows = (await db.execute(
        select(Event.data, Event.created_at).where(Event.thread_id == thread_id, Event.type == EVENT).order_by(Event.seq)
    )).all()
    by_rev: dict[int, dict] = {}
    for data, created in rows:
        if isinstance(data, dict) and data.get("page_id") == page_id:
            version = _version(data, created.isoformat() if created else None)
            if version is not None:
                by_rev[version["rev"]] = version
    if not by_rev:
        await db.rollback()
        raise SavedAppError(404, "not_found", "No such page in this conversation.")
    count = len(list((await db.execute(select(SavedApp.id).where(SavedApp.owner_id == owner_id))).scalars()))
    if count >= APPS_MAX:
        await db.rollback()
        raise SavedAppError(507, "store_full", f"You can keep at most {APPS_MAX} apps. Delete one first.")
    versions = trim(list(by_rev.values()), None)
    newest = versions[-1]
    now = datetime.now(UTC)
    app = SavedApp(
        id=new_id(), owner_id=owner_id, agent_id=thread.agent_id, thread_id=thread_id, page_id=page_id,
        name=(newest["title"] or "Untitled app")[:NAME_MAX], pinned_rev=None, versions=versions,
        uses=newest["uses"], created_at=now, updated_at=now,
    )
    db.add(app)
    await db.commit()
    return app, True


async def append_version(db, owner_id: str, page_id: str, data: dict) -> bool:
    """Add a new revision of a page to the app saved from it, if there is one. Deduplicated by rev."""
    version = _version(data)
    if version is None:
        return False
    await begin_write(db)
    app = (await db.execute(
        select(SavedApp).where(SavedApp.owner_id == owner_id, SavedApp.page_id == page_id))).scalar_one_or_none()
    if app is None or any(v["rev"] == version["rev"] for v in app.versions or []):
        await db.rollback()
        return False
    app.versions = trim([*(app.versions or []), version], app.pinned_rev)
    app.uses = version["uses"]
    app.updated_at = datetime.now(UTC)
    await db.commit()
    return True


async def update(db, app: SavedApp, *, name: str | None, pinned_rev: int | None, set_pin: bool) -> SavedApp:
    if name is not None:
        app.name = name
    if set_pin:
        if pinned_rev is not None and all(v["rev"] != pinned_rev for v in app.versions or []):
            await db.rollback()
            raise SavedAppError(422, "unknown_rev", "That version is not kept.")
        app.pinned_rev = pinned_rev
    app.updated_at = datetime.now(UTC)
    await db.commit()
    return app


def pick(app: SavedApp, rev: int | None) -> dict | None:
    versions = app.versions or []
    wanted = rev if rev is not None else app.pinned_rev
    if wanted is None:
        return versions[-1] if versions else None
    return next((v for v in versions if v["rev"] == wanted), None)
