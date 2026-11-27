"""A small stdio MCP server giving a turn two tools to show the person a small
self-contained HTML page inline in the chat: `show_page` and `update_page`.

It never touches the database or the agent's files. Each call is a request file in
the turn's private mailbox folder; the backend (app/services/pages_bridge.py)
validates it, records a `page.shown` event for the thread and writes the reply
beside it. The same transport as the memory tools (app/memory_mcp_server.py).
The page is only rendered, sandboxed, by the app: no approval is consumed.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_SHARED = {
    "html": {"type": "string", "maxLength": 262144,
             "description": "One complete, self-contained HTML document or fragment: inline CSS and JS only, no external files."},
    "height": {"type": "integer", "minimum": 80, "maximum": 720, "description": "Height in pixels; default 360."},
    "frameless": {"type": "boolean", "description": "Show the page without a card frame around it."},
    "uses": {"type": "array", "maxItems": 25, "items": {"type": "string", "maxLength": 200},
             "description": "Capabilities the page asks for. \"state\", and \"toolkit:TOOL\" (for example \"gmail:GMAIL_FETCH_EMAILS\") for each connected-app tool the page may call; at most 8 apps and 24 tools."},
}

TOOLS = [
    {
        "name": "show_page",
        "description": (
            "Show the person a small self-contained HTML page inline in this conversation (a chart, a form, "
            "a calculator, a mock-up). It runs in a sandbox with no network access. Returns the page_id; "
            "use update_page with it to change the page later."
        ),
        "inputSchema": _schema({
            **_SHARED,
            "title": {"type": "string", "maxLength": 80, "description": "A short title for the page."},
        }, ("html", "title")),
    },
    {
        "name": "update_page",
        "description": "Replace a page you already showed in this conversation, using the page_id show_page gave you.",
        "inputSchema": _schema({
            "page_id": {"type": "string", "maxLength": 64},
            **_SHARED,
            "title": {"type": "string", "maxLength": 80, "description": "A new title; leave out to keep it."},
        }, ("page_id", "html")),
    },
]


class Server:
    def __init__(self, mailbox):
        self.mailbox = Path(mailbox)

    def call(self, name, arguments):
        if name not in {t["name"] for t in TOOLS} or not isinstance(arguments, dict):
            return {"error": "Unknown tool or invalid arguments."}
        token = uuid4().hex
        path = self.mailbox / f"req-{token}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"name": name, "arguments": arguments}), encoding="utf-8")
        os.replace(temporary, path)
        response = self.mailbox / f"resp-{token}.json"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if response.exists():
                result = json.loads(response.read_text(encoding="utf-8"))
                response.unlink(missing_ok=True)
                return result
            time.sleep(0.05)
        return {"error": "The page request timed out."}

    def handle(self, message):
        method, identifier = message.get("method"), message.get("id")
        if identifier is None:
            return None
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "pages", "version": "1"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params") or {}
            body = self.call(params.get("name"), params.get("arguments") or {})
            text = body.get("text") if isinstance(body.get("text"), str) else json.dumps(body, ensure_ascii=False)
            result = {"content": [{"type": "text", "text": text}], "isError": bool(body.get("error"))}
        else:
            return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32601, "message": "Unknown method"}}
        return {"jsonrpc": "2.0", "id": identifier, "result": result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mailbox", required=True)
    server = Server(parser.parse_args().mailbox)
    for line in sys.stdin:
        try:
            if len(line) > 400000:
                continue
            result = server.handle(json.loads(line))
            if result is not None:
                print(json.dumps(result), flush=True)
        except (ValueError, OSError, TypeError):
            continue


if __name__ == "__main__":
    main()
