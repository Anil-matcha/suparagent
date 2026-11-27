"""One turn's mailbox for the page tools (show_page, update_page).

The engine's MCP server (app/pages_mcp_server.py) drops request files in a private
folder; this polls them, validates them and records a `page.shown` event on the
thread, which the chat renders as a sandboxed inline page. update_page records
the same event type again with the same page_id and the next rev. Nothing here
touches the agent's files or the database beyond reading the thread, so no
approval is consumed (tool_policy allows `mcp__pages__*` automatically, except in
a draft-only run). Pages are refused in rooms.
"""
import asyncio
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.core.logging import get_logger

logger = get_logger(__name__)

SERVER = "pages"
TOOL_PREFIX = f"mcp__{SERVER}__"
TOOLS = ("show_page", "update_page")
EVENT = "page.shown"
HTML_MAX_BYTES = 262144
TITLE_MAX = 80
HEIGHT_MIN, HEIGHT_MAX, HEIGHT_DEFAULT = 80, 720, 360
USES_MAX, USES_ENTRY_MAX = 25, 200
PAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
REQUEST_MAX_BYTES = 400_000
TOOL_NOTE = (
    "To show the person something visual or interactive inline in this conversation (a chart, a form, a calculator, "
    "a mock-up), call show_page with a title and one self-contained HTML page (inline CSS and JS, no external files). "
    "It runs in a sandbox. To change a page you already showed, call update_page with its page_id. Keep the reply "
    "text short; the page is the answer."
)
_ARGS = {"html", "title", "height", "frameless", "uses", "page_id"}
TOOLKIT_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,63}$")
TOOL_RE = re.compile(r"^[A-Z0-9_]{1,120}$")
CONNECTORS_MAX, CONNECTOR_TOOLS_MAX = 8, 24
_TOOL_SUFFIX_BLOCKED = ("_MANAGE_CONNECTIONS", "_WAIT_FOR_CONNECTIONS")


def parse_connector(entry: object) -> tuple[str, str] | None:
    """(toolkit, TOOL) for a well-formed "toolkit:TOOL" entry, else None. No wildcards, never the
    `composio` toolkit, and the tool must start with its toolkit's name in upper case."""
    if not isinstance(entry, str) or entry.count(":") != 1:
        return None
    toolkit, tool = entry.split(":")
    if not TOOLKIT_RE.match(toolkit) or toolkit == "composio" or not TOOL_RE.match(tool):
        return None
    if not tool.startswith(toolkit.upper().replace("-", "_") + "_") or tool.endswith(_TOOL_SUFFIX_BLOCKED):
        return None
    return toolkit, tool


def declared_connectors(uses: object) -> list[dict]:
    """The connected-app tools a page declared, as [{"toolkit", "tools": [TOOL, ...]}], in first-seen order.
    Lenient on purpose: stored values from before this grammar (other strings, "state") are skipped, never an
    error. Counts beyond the caps are dropped, so a stored value can never declare more than a new one may."""
    groups: dict[str, list[str]] = {}
    for entry in uses if isinstance(uses, list) else []:
        parsed = parse_connector(entry)
        if parsed is None:
            continue
        toolkit, tool = parsed
        if toolkit not in groups and len(groups) >= CONNECTORS_MAX:
            continue
        if tool not in groups.setdefault(toolkit, []):
            groups[toolkit].append(tool)
    out, total = [], 0
    for toolkit, tools in groups.items():
        tools = tools[: max(0, CONNECTOR_TOOLS_MAX - total)]
        total += len(tools)
        if tools:
            out.append({"toolkit": toolkit, "tools": tools})
    return out


class PageError(ValueError):
    """A readable refusal the model can act on."""


def is_pages_call(*, server: str | None = None, tool: str | None = None, title: str | None = None, raw=None) -> bool:
    """True when a permission request is for one of the two page tools of this turn's
    own server. An engine names a tool its own way (`mcp__pages__show_page`,
    `pages/show_page`, "show_page (pages MCP Server)"), so a call counts only when its
    name is one of the tools, it comes from the server called `pages`, and its
    arguments are only the ones those tools take."""
    if isinstance(raw, dict) and not set(raw) <= _ARGS:
        return False
    if tool is not None:
        return server == SERVER and tool in TOOLS
    text = (title or "").lower()
    return len(text) <= 200 and SERVER in text and any(name in text for name in TOOLS)


def _check_common(args: dict, *, creating: bool) -> dict:
    out: dict = {}
    html = args.get("html")
    if not isinstance(html, str) or not html.strip():
        raise PageError("html must be a non-empty string with the whole page.")
    if len(html.encode("utf-8")) > HTML_MAX_BYTES:
        raise PageError(f"html is too large: keep it under {HTML_MAX_BYTES} bytes (256 KiB) and inline only what the page needs.")
    out["html"] = html
    title = args.get("title")
    if title is None:
        if creating:
            raise PageError("title is required: a short name for the page, at most 80 characters.")
    else:
        if not isinstance(title, str) or not title.strip():
            raise PageError("title must be a short non-empty string.")
        if len(title) > TITLE_MAX:
            raise PageError(f"title is too long: use at most {TITLE_MAX} characters.")
        out["title"] = title.strip()
    height = args.get("height")
    if height is not None:
        if isinstance(height, bool) or not isinstance(height, int) or not HEIGHT_MIN <= height <= HEIGHT_MAX:
            raise PageError(f"height must be a whole number of pixels from {HEIGHT_MIN} to {HEIGHT_MAX}.")
        out["height"] = height
    frameless = args.get("frameless")
    if frameless is not None:
        if not isinstance(frameless, bool):
            raise PageError("frameless must be true or false.")
        out["frameless"] = frameless
    uses = args.get("uses")
    if uses is not None:
        if not isinstance(uses, list) or len(uses) > USES_MAX:
            raise PageError(f"uses must be a list of at most {USES_MAX} strings.")
        if not all(isinstance(u, str) and 0 < len(u) <= USES_ENTRY_MAX for u in uses):
            raise PageError(f"every uses entry must be a non-empty string of at most {USES_ENTRY_MAX} characters.")
        unique = list(dict.fromkeys(uses))
        for entry in unique:
            if entry != "state" and parse_connector(entry) is None:
                raise PageError(
                    f'uses entry "{entry[:60]}" is not recognised: use "state", or "toolkit:TOOL" for one tool of a '
                    'connected app (for example "gmail:GMAIL_FETCH_EMAILS"): lower-case toolkit, the exact upper-case '
                    "tool name starting with the toolkit name, no wildcards.")
        groups = declared_connectors(unique)
        if sum(1 for e in unique if parse_connector(e)) > sum(len(g["tools"]) for g in groups) or len(
                {e.split(":")[0] for e in unique if parse_connector(e)}) > CONNECTORS_MAX:
            raise PageError(f"uses may name at most {CONNECTORS_MAX} connected apps and {CONNECTOR_TOOLS_MAX} tools in all.")
        out["uses"] = unique
    return out


class PagesBridge:
    def __init__(self, runtime, run):
        self.runtime, self.run = runtime, run
        self.folder = Path(tempfile.mkdtemp(prefix=f"suparagent-pages-{run.turn_id}-"))
        self.task: asyncio.Task | None = None
        self.calls: set[asyncio.Task] = set()
        # Pages shown by this turn, by id (earlier turns' pages are read back from the thread's events).
        self.pages: dict[str, dict] = {}
        self.lock = asyncio.Lock()

    @property
    def launch(self):
        if getattr(sys, "frozen", False):
            return SimpleNamespace(command=sys.executable, args=["--pages-mcp", "--mailbox", str(self.folder)], env={})
        root = str(Path(__file__).resolve().parents[2])
        return SimpleNamespace(
            command=sys.executable, args=["-m", "app.pages_mcp_server", "--mailbox", str(self.folder)],
            env={"PYTHONPATH": root},
        )

    def start(self) -> None:
        self.task = asyncio.create_task(self.poll())

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for task in self.calls:
            task.cancel()
        await asyncio.gather(*self.calls, return_exceptions=True)
        shutil.rmtree(self.folder, ignore_errors=True)

    async def poll(self) -> None:
        while not self.run.ctx.aborted:
            for path in self.folder.glob("req-*.json"):
                claimed = path.with_suffix(".working")
                try:
                    path.rename(claimed)
                except FileNotFoundError:
                    continue
                task = asyncio.create_task(self.handle(claimed))
                self.calls.add(task)
                task.add_done_callback(self.calls.discard)
            await asyncio.sleep(0.05)

    async def handle(self, path: Path) -> None:
        try:
            if path.stat().st_size > REQUEST_MAX_BYTES:
                result = {"error": "The request is too large."}
            else:
                request = json.loads(path.read_text(encoding="utf-8"))
                name, args = request["name"], request["arguments"]
                if not isinstance(name, str) or not isinstance(args, dict):
                    raise ValueError("invalid")
                result = await self.execute(name, args)
        except Exception:  # noqa: BLE001
            logger.exception("a pages call failed", turn_id=self.run.turn_id)
            result = {"error": "The page could not be shown."}
        response = self.folder / path.name.replace("req-", "resp-").replace(".working", ".json")
        tmp = response.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, response)
        path.unlink(missing_ok=True)

    async def execute(self, name: str, args: dict) -> dict:
        if name not in TOOLS:
            return {"error": "Unknown tool."}
        run, rt = self.run, self.runtime
        from app.db.models import Thread

        async with rt.session() as db:
            thread = await db.get(Thread, run.thread_id)
        if thread is None or thread.room_id is not None:
            return {"error": "Pages cannot be shown in a room. Answer in text instead."}
        try:
            if name == "show_page":
                fields = _check_common(args, creating=True)
                page_id = secrets.token_hex(6)
                data = {"page_id": page_id, "rev": 1, "title": fields["title"], "html": fields["html"],
                        "height": fields.get("height", HEIGHT_DEFAULT), "frameless": fields.get("frameless", False),
                        "uses": fields.get("uses", [])}
            else:
                page_id = args.get("page_id")
                if not isinstance(page_id, str) or not PAGE_ID.match(page_id):
                    raise PageError("page_id must be the id show_page returned (letters, digits, - and _, at most 64).")
                fields = _check_common(args, creating=False)
                old = await self.current(page_id)
                if old is None:
                    raise PageError(f"No page {page_id} was shown in this conversation. Call show_page to show a new one.")
                data = {**old, **fields, "page_id": page_id, "rev": int(old["rev"]) + 1}
        except PageError as refused:
            return {"error": str(refused)}
        async with self.lock:
            # Re-read inside the lock so two quick updates cannot take the same rev.
            if name == "update_page":
                old = await self.current(data["page_id"])
                data["rev"] = int(old["rev"]) + 1 if old else data["rev"]
            if not await run.recorder.emit(EVENT, data):
                return {"error": "The page could not be shown."}
            self.pages[data["page_id"]] = data
        if name == "update_page":
            await self.follow(data)
        verb = "Showed" if name == "show_page" else "Updated"
        return {"text": f'{verb} page "{data["title"]}" (page_id {data["page_id"]}, rev {data["rev"]}). '
                        "Use update_page with this page_id to change it."}

    async def follow(self, data: dict) -> None:
        """Add the new revision to the app saved from this page, if any. Never fails the tool call."""
        try:
            from app.services import saved_apps

            async with self.runtime.session() as db:
                await saved_apps.append_version(db, self.runtime.owner_id, data["page_id"], data)
        except Exception:  # noqa: BLE001
            logger.exception("a saved app could not follow its page", turn_id=self.run.turn_id)

    async def current(self, page_id: str) -> dict | None:
        """The newest data of a page this thread showed, from this turn or an earlier one."""
        if page_id in self.pages:
            return self.pages[page_id]
        from sqlalchemy import select
        from app.db.models import Event

        async with self.runtime.session() as db:
            rows = (await db.execute(
                select(Event.data).where(Event.thread_id == self.run.thread_id, Event.type == EVENT).order_by(Event.seq.desc())
            )).scalars().all()
        for data in rows:
            if isinstance(data, dict) and data.get("page_id") == page_id:
                return data
        return None
