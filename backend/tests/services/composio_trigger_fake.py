"""A Composio fake for triggers and webhooks, with the shapes seen against a real project (BL-177).

Not a test module: imported by the client, service and router tests."""
import json

import httpx

PREFIX = "/api/v3.1"
SECRET_LENGTH = 64


class TriggerFake:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.accounts = [{"id": "ca_g", "toolkit": {"slug": "gmail"}, "alias": None, "status": "ACTIVE", "user_id": "u1"}]
        self.triggers: dict[str, dict] = {}
        self.subs: dict[str, dict] = {}
        self.allow_many_subs = True
        self.fail: dict[str, int] = {}          # "METHOD /path-prefix" -> status to answer with
        self.bodies: list[dict] = []
        self._n = 0

    def next_id(self, prefix):
        self._n += 1
        return f"{prefix}_{self._n:04d}"

    def mutations(self):
        return [c for c in self.calls if c[0] != "GET"]

    def handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path.removeprefix(PREFIX)
        self.calls.append((method, path))
        for key, status in self.fail.items():
            wanted_method, _, prefix = key.partition(" ")
            if method == wanted_method and path.startswith(prefix):
                return httpx.Response(status, json={"error": {"message": f"forced {status}"}})
        body = json.loads(request.content) if request.content else {}
        if body:
            self.bodies.append(body)
        if (method, path) == ("GET", "/connected_accounts"):
            slug = request.url.params.get("toolkit_slugs")
            return httpx.Response(200, json={"items": [a for a in self.accounts if slug is None or a["toolkit"]["slug"] == slug]})
        if (method, path) == ("GET", "/triggers_types"):
            return httpx.Response(200, json={"items": [
                {"slug": "GMAIL_EMAIL_SENT_TRIGGER", "type": "poll", "config": {"properties": {"interval": {}, "query": {}, "userId": {}}}},
                {"slug": "GMAIL_NEW_GMAIL_MESSAGE", "type": "poll", "config": {"properties": {"interval": {}, "labelIds": {}, "query": {}, "userId": {}}}}]})
        if method == "POST" and path.startswith("/trigger_instances/") and path.endswith("/upsert"):
            config = body.get("trigger_config") or {}
            for trigger in self.triggers.values():  # an identical setting returns the existing instance
                if trigger["connected_account_id"] == body["connected_account_id"] and trigger["trigger_config"] == config:
                    return httpx.Response(201, json={"trigger_id": trigger["id"], "polling_interval": 2, "type": "poll", "fallback_reason": None})
            trigger_id = self.next_id("ti")
            self.triggers[trigger_id] = {"id": trigger_id, "connected_account_id": body["connected_account_id"], "trigger_config": config,
                                         "triggerName": path.split("/")[2], "disabled_at": None, "polling_interval": 2}
            return httpx.Response(201, json={"trigger_id": trigger_id, "polling_interval": 2, "type": "poll", "fallback_reason": None})
        if (method, path) == ("GET", "/trigger_instances/active"):
            wanted = request.url.params.get("connected_account_ids")
            items = [t for t in self.triggers.values() if (not wanted or t["connected_account_id"] in wanted.split(","))
                     and (request.url.params.get("show_disabled") == "true" or not t["disabled_at"])]
            return httpx.Response(200, json={"items": items, "next_cursor": None})
        if path.startswith("/trigger_instances/manage/"):
            trigger_id = path.rsplit("/", 1)[1]
            if trigger_id not in self.triggers:
                return httpx.Response(404, json={"error": {"message": "no such trigger"}})
            if method == "DELETE":
                del self.triggers[trigger_id]
                return httpx.Response(200, json={"status": "success"})
            disabled = body.get("status") == "disable"
            if bool(self.triggers[trigger_id]["disabled_at"]) == disabled:
                return httpx.Response(409, json={"error": {"message": "already in that state"}})
            self.triggers[trigger_id]["disabled_at"] = "2026-11-26T00:00:00Z" if disabled else None
            return httpx.Response(200, json={"status": "success"})
        if (method, path) == ("GET", "/webhook_subscriptions"):
            return httpx.Response(200, json={"items": list(self.subs.values()), "total_items": len(self.subs), "next_cursor": None})
        if (method, path) == ("POST", "/webhook_subscriptions"):
            url = body.get("webhook_url", "")
            if "invalid" in url:
                return httpx.Response(400, json={"error": {"message": 'SSRF protection: unable to verify the hostname "x" resolves to a safe address',
                                                           "slug": "WebhookSubscription_InvalidWebhookURL"}})
            if self.subs and not self.allow_many_subs:
                return httpx.Response(400, json={"error": {"message": "A project can have only one webhook subscription"}})
            sub_id = self.next_id("ws")
            self.subs[sub_id] = {"id": sub_id, "webhook_url": url, "version": "V3", "enabled_events": body.get("enabled_events", []),
                                 "secret": (sub_id.encode().hex() * 8)[:SECRET_LENGTH]}
            return httpx.Response(201, json=self.subs[sub_id])
        if path.startswith("/webhook_subscriptions/"):
            sub_id = path.rsplit("/", 1)[1]
            if sub_id not in self.subs:
                return httpx.Response(404, json={"error": {"message": "no such webhook"}})
            if method == "DELETE":
                del self.subs[sub_id]
                return httpx.Response(200, json={"status": "success"})
            return httpx.Response(200, json=self.subs[sub_id])
        return httpx.Response(404, json={"error": {"message": f"unrouted {method} {path}"}})
