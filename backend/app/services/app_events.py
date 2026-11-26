"""A routine's new-mail trigger (BL-177): the owner side of Composio's Gmail "new message" events.

Composio watches the connected Gmail account itself (its trigger type is a server-side poll) and pushes each event to
ONE webhook in the owner's own Composio project. This module creates and removes the two remote objects, keeps the
local rows that say which routine they belong to, and answers "can this install receive events at all". Nothing here
polls Gmail, and nothing reads message text: the receiver (routers/composio_hooks.py, routine_service.accept_app_event)
keeps ids only.

Remote objects, both in the owner's project and both created with the owner's own key:
  * the project's webhook subscription: ours is `<public origin>/hooks/composio/<route id>`. It is created only when the
    project has none of ours, and an address we did not create is never changed or deleted;
  * one trigger instance per routine (`app_event_trigger`), filtered at the source by the owner's search.

The webhook's route id and signing secret live in the encrypted Composio record (composio_service.load/_save) under
`webhook`, beside the key. `webhook_route_for` and `webhook_secret_for` are what the receiver asks for.

Clean-up is conservative on purpose. A remote trigger instance is only ever deleted when this app created it: when a
delete cannot be completed (Composio down) its id is kept in the record under `pending_trigger_deletes` and retried by
`reconcile`. Instances that exist in the project but that this app never made are counted and left alone.
"""
import asyncio
import re
import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from app import composio_tools
from app.core.config import settings
from app.db import tenant_schema
from app.db.models import Agent, AppEventDay, AppEventTrigger, Routine
from app.services import composio_service
from app.services.composio_client import TRIGGER_EVENT, WEBHOOK_EVENTS, ComposioError

APP = "gmail"
SLUG = "GMAIL_NEW_GMAIL_MESSAGE"
QUERY_MAX = 200
PENDING_MAX = 50
DEBOUNCE_RANGE = (1, 60)
RUNS_RANGE = (1, 48)
COUNTERS = ("received", "started", "merged", "duplicate", "outside_share", "rejected", "unrouted", "dropped_cap", "dropped_quiet")

_ACCOUNT = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ROUTE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_KEYS = {"app", "account_id", "query", "debounce_minutes", "max_runs_per_day", "quiet"}

_subscription_lock = asyncio.Lock()  # one webhook subscription is created at a time


class AppEventError(Exception):
    """A refusal or failure the owner can act on, in plain words. `status` is the HTTP status to answer with."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _mapped(exc: ComposioError) -> AppEventError:
    """A Composio failure as the same plain-words error the Apps page shows (no response text beyond its short message)."""
    problem = composio_service._user_error(exc)
    return AppEventError(problem.code, problem.message, problem.status)


# -- Where events can arrive -------------------------------------------------------------------------------------------

def public_origin() -> str:
    """The address Composio can reach this server at (SUPARAGENT_PUBLIC_URL), or ''."""
    return settings.SUPARAGENT_PUBLIC_URL or ""


def webhook_url(route_id: str) -> str | None:
    """Where Composio posts events. A hosted workspace names its account in the path, like the routine webhooks do
    (routers/hooks.py); the receiver must serve both shapes."""
    origin = public_origin()
    if not origin:
        return None
    tenant = tenant_schema.current_tenant()
    return origin + (f"/hooks/t/{tenant[2:]}/composio/{route_id}" if tenant else f"/hooks/composio/{route_id}")


def endpoint_problem() -> tuple[str, str] | None:
    """(code, plain words) when this install cannot receive Composio's events, else None."""
    origin = public_origin()
    if not origin:
        return ("no_public_endpoint",
                "Composio sends new-mail events to a public web address, and this install has none. A hosted workspace or a "
                "server with a public address can use new-mail wakes; a desktop app will need the relay, which is not ready "
                "yet. The routine keeps running on its schedule.")
    if not origin.startswith("https://"):
        return ("https_required", "Composio only sends events to an https address. Give this server an https public address first.")
    return None


# -- The webhook this app keeps in the owner's project ---------------------------------------------------------------

def _hook_of(record: dict | None) -> dict | None:
    hook = (record or {}).get("webhook")
    if (isinstance(hook, dict) and isinstance(hook.get("route_id"), str) and _ROUTE.match(hook["route_id"])
            and all(isinstance(hook.get(k), str) and hook[k] for k in ("subscription_id", "url", "secret"))):
        return hook
    return None


async def webhook_secret_for(db, owner_id: str) -> str | None:
    """The secret the receiver checks signatures with, or None when this app has no webhook of its own."""
    hook = _hook_of(await composio_service.load(db, owner_id))
    return hook["secret"] if hook else None


async def webhook_route_for(db, owner_id: str) -> str | None:
    """The random route id in this owner's webhook address; a delivery to any other route is refused."""
    hook = _hook_of(await composio_service.load(db, owner_id))
    return hook["route_id"] if hook else None


async def _update_record(db, owner_id: str, change) -> dict:
    """Read, change and save the encrypted Composio record under the service's own lock, so a session refresh cannot
    be overwritten. `change(record)` edits the copy in place."""
    async with composio_service.lock:
        record = await composio_service.load(db, owner_id)
        if record is None:
            raise AppEventError("not_configured", "Connected apps are not set up. Add your Composio key on the Apps page first.", 409)
        record = dict(record)
        change(record)
        await composio_service._save(db, owner_id, record)
        return record


async def _ensure_subscription(db, owner_id: str, client) -> dict:
    """OUR webhook in the owner's project: reused when it exists, created when there is none, never altered otherwise."""
    problem = endpoint_problem()
    if problem:
        raise AppEventError(problem[0], problem[1], 409)
    async with _subscription_lock:
        record = await composio_service.load(db, owner_id)
        hook = _hook_of(record)
        remote = await client.list_webhook_subscriptions()
        if hook:
            match = next((s for s in remote if s.id == hook["subscription_id"]), None)
            if match is not None:
                if match.url != hook["url"] or TRIGGER_EVENT not in match.events:
                    raise AppEventError("webhook_changed", "The event webhook in your Composio project was changed outside this "
                                                           "app. This app will not overwrite it. Remove it in Composio and try again.", 409)
                return hook
            route_id, url = hook["route_id"], hook["url"]  # it was deleted in Composio: recreate it at the same address
        else:
            prefix = (webhook_url("") or "")
            ours = next((s for s in remote if prefix and s.url.startswith(prefix) and _ROUTE.match(s.url[len(prefix):] or "")), None)
            if ours is not None:  # a record lost in a key change: adopt our own address again (the secret can be read back)
                full = await client.get_webhook_subscription(ours.id)
                route_id = ours.url[len(prefix):]
                adopted = {"route_id": route_id, "subscription_id": full.id, "url": full.url, "secret": full.secret or ""}
                if not adopted["secret"]:
                    raise AppEventError("webhook_changed", "Composio did not return the signing secret of this app's webhook.", 409)
                await _update_record(db, owner_id, lambda r: r.__setitem__("webhook", adopted))
                return adopted
            route_id = secrets.token_urlsafe(24)
            url = webhook_url(route_id)
        try:
            created = await client.create_webhook_subscription(url, WEBHOOK_EVENTS)
        except ComposioError as exc:
            if exc.kind == "rejected":
                detail = exc.message or ""
                if "SSRF" in detail or "InvalidWebhookURL" in detail or "resolve" in detail:
                    raise AppEventError("webhook_rejected", "Composio could not reach this server's public address. It must be an https "
                                                            "address that resolves on the internet.", 409) from None
                if remote:
                    raise AppEventError("webhook_in_use", "This Composio project already sends events to another address. This app never "
                                                          "replaces it. Use a separate Composio project, or remove that webhook yourself first.", 409) from None
            raise _mapped(exc) from None
        hook = {"route_id": route_id, "subscription_id": created.id, "url": created.url, "secret": created.secret}
        await _update_record(db, owner_id, lambda r: r.__setitem__("webhook", hook))
        return hook


async def last_trigger_removed_cleanup(db, owner_id: str, client) -> bool:
    """Once no routine listens for mail any more, delete OUR webhook (its address must match the one we made)."""
    async with _subscription_lock:
        if (await db.execute(select(AppEventTrigger.id).where(AppEventTrigger.owner_id == owner_id,
                                                              AppEventTrigger.state != "removed").limit(1))).first():
            return False
        hook = _hook_of(await composio_service.load(db, owner_id))
        if not hook:
            return False
        try:
            remote = await client.list_webhook_subscriptions()
            match = next((s for s in remote if s.id == hook["subscription_id"]), None)
            if match is not None and match.url == hook["url"]:
                await client.delete_webhook_subscription(match.id)
        except ComposioError:
            return False  # still ours; the next clean-up tries again
        await _update_record(db, owner_id, lambda r: r.pop("webhook", None))
        return True


# -- Validation --------------------------------------------------------------------------------------------------------

def normalize(config: object) -> dict:
    """The routine's `app_event` setting, strictly checked and with its defaults filled in."""
    if not isinstance(config, dict) or set(config) - _KEYS:
        raise AppEventError("invalid_app_event", "The new-mail setting has fields this app does not know.")
    if config.get("app", APP) != APP:
        raise AppEventError("invalid_app_event", "Only Gmail can wake a routine today.")
    account = config.get("account_id")
    if not isinstance(account, str) or not _ACCOUNT.match(account):
        raise AppEventError("invalid_app_event", "Choose which Gmail account the routine listens to.")
    query = config.get("query") or ""
    if not isinstance(query, str) or len(query.strip()) > QUERY_MAX or _CONTROL.search(query):
        raise AppEventError("invalid_app_event", f"The mail search must be plain text of at most {QUERY_MAX} characters.")
    out = {"app": APP, "account_id": account, "query": query.strip()}
    for key, (low, high), default in (("debounce_minutes", DEBOUNCE_RANGE, 5), ("max_runs_per_day", RUNS_RANGE, 12)):
        value = config.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise AppEventError("invalid_app_event", f"{key.replace('_', ' ').capitalize()} must be a whole number from {low} to {high}.")
        out[key] = value
    quiet = config.get("quiet")
    if quiet is not None:
        if (not isinstance(quiet, dict) or set(quiet) != {"from", "to"} or not all(isinstance(quiet[k], str) and _CLOCK.match(quiet[k]) for k in quiet)
                or quiet["from"] == quiet["to"]):
            raise AppEventError("invalid_app_event", "Quiet hours need two different times such as 20:00 and 07:00.")
        out["quiet"] = {"from": quiet["from"], "to": quiet["to"]}
    return out


async def check(db, owner_id: str, routine: Routine, config: dict) -> None:
    """Every local precondition, in plain words, before any remote call. Raises AppEventError."""
    from app.composio_tools import BY_NAME
    from app.schemas.routine import ConnectedAppGrant, validate_draft_grants
    from app.services.composio_bridge import accounts_for, apps_for

    account = config["account_id"]
    agent = await db.get(Agent, routine.agent_id)
    if agent is None or agent.owner_id != owner_id or agent.archived_at is not None:
        raise AppEventError("agent_unavailable", "Choose an available agent for this routine first.")
    if (apps_for(agent.connector_scopes).get(APP) not in composio_tools.LEVELS
            or account not in accounts_for(agent.connector_account_scopes).get(APP, [])):
        raise AppEventError("agent_cannot_read_mail", "The agent is not allowed to read that Gmail account yet. Allow it under the agent's "
                                                      "connected apps, then turn the new-mail wake on.")
    grants = routine.connected_app_grants or []
    reads = [g for g in grants if isinstance(g, dict) and g.get("account_id") == account
             and g.get("tool") in BY_NAME and BY_NAME[g["tool"]].app == APP and BY_NAME[g["tool"]].kind == "read"]
    if not routine.uninterrupted_access and not reads:
        raise AppEventError("routine_cannot_read_mail", "When the routine runs on its own it needs permission to read that Gmail account: add a "
                                                        "Gmail read action for it, or turn on uninterrupted access.")
    if any(isinstance(g, dict) and g.get("tool") == "gmail_prepare_checked_reply" for g in grants):
        try:
            validate_draft_grants([ConnectedAppGrant.model_validate(g) for g in grants])
        except ValueError as exc:
            raise AppEventError("invalid_app_grant", str(exc)) from None
        if {g.get("account_id") for g in grants if isinstance(g, dict)} != {account}:
            raise AppEventError("mailbox_mismatch", "This routine drafts replies for one mailbox, and the new-mail wake must listen to that same account.")


# -- Status ------------------------------------------------------------------------------------------------------------

def _zero() -> dict:
    return {name: 0 for name in COUNTERS}


async def _trigger(db, routine_id: int) -> AppEventTrigger | None:
    return (await db.execute(select(AppEventTrigger).where(AppEventTrigger.routine_id == routine_id))).scalar_one_or_none()


async def status_for(db, routine: Routine) -> dict:
    """What the routine editor shows: state, last event, last error, today's counts and whether this install can receive events.
    Counts only: no ids, senders or text."""
    row = await _trigger(db, routine.id)
    problem = endpoint_problem()
    today = _zero()
    if row is not None:
        day = (await db.execute(select(AppEventDay).where(AppEventDay.trigger_id == row.id,
                                                          AppEventDay.day == datetime.now(UTC).date()))).scalar_one_or_none()
        if day is not None:
            today = {name: getattr(day, name) for name in COUNTERS}
    return {
        "state": row.state if row is not None else "off", "last_event_at": row.last_event_at if row else None,
        "last_error": row.last_error if row else None, "today": today,
        "can_receive": problem is None, "reason": problem[0] if problem else None, "message": problem[1] if problem else None,
    }


# -- Enable, pause, resume, remove -------------------------------------------------------------------------------------

async def _client(db, owner_id: str, make_client):
    record = await composio_service.load(db, owner_id)
    if record is None:
        raise AppEventError("not_configured", "Connected apps are not set up. Add your Composio key on the Apps page first.", 409)
    return record, make_client(record["api_key"])


async def enable(db, owner_id: str, routine: Routine, make_client, config: object) -> AppEventTrigger:
    """Turn the new-mail wake on for a routine, or change it. Safe to repeat: the same setting finds the same instance."""
    cfg = normalize(config)
    await check(db, owner_id, routine, cfg)
    problem = endpoint_problem()
    if problem:
        raise AppEventError(problem[0], problem[1], 409)
    record, client = await _client(db, owner_id, make_client)
    try:
        accounts = await client.accounts(record["user_id"], APP)
        if not any(a.id == cfg["account_id"] and a.status == "ACTIVE" for a in accounts):
            raise AppEventError("account_not_connected", "That Gmail account is not connected right now. Connect it again on the Apps page.", 409)
        await _ensure_subscription(db, owner_id, client)
        instance = await client.upsert_trigger(SLUG, cfg["account_id"], {"query": cfg["query"]} if cfg["query"] else {})
    except ComposioError as exc:
        raise _mapped(exc) from None
    other = (await db.execute(select(AppEventTrigger).where(AppEventTrigger.composio_trigger_id == instance.id,
                                                            AppEventTrigger.routine_id != routine.id))).scalar_one_or_none()
    if other is not None:  # an identical setting returns the same instance, so two routines cannot share one
        raise AppEventError("trigger_in_use", "Another routine already listens for the same mail (same account and search). Change the search, or use that routine.", 409)
    row = await _trigger(db, routine.id)
    replaced = None
    if row is None:
        row = AppEventTrigger(owner_id=owner_id, routine_id=routine.id, composio_trigger_id=instance.id, composio_account_id=cfg["account_id"], slug=SLUG)
        db.add(row)
    elif row.composio_trigger_id != instance.id:
        replaced = row.composio_trigger_id
    row.composio_trigger_id, row.composio_account_id = instance.id, cfg["account_id"]
    row.query, row.polling_interval, row.last_error = cfg["query"], instance.polling_interval, None
    row.state = "paused" if routine.paused else "active"
    routine.app_event = cfg
    await db.commit()
    if routine.paused:
        await _remote_enabled(client, instance.id, False)
    if replaced:
        await _delete_remote(db, owner_id, client, replaced)
    return row


async def _remote_enabled(client, trigger_id: str, enabled: bool) -> str | None:
    """Best effort: a failure is reported as a short reason, never raised (a pause must not fail because Composio is down)."""
    try:
        await client.set_trigger_enabled(trigger_id, enabled)
        return None
    except ComposioError as exc:
        return (exc.message or "Composio did not answer.")[:200]


async def _delete_remote(db, owner_id: str, client, trigger_id: str, *, remember: bool = True) -> bool:
    """Delete an instance this app made. If Composio cannot be reached and no local row will hold the id for a retry
    (`remember`), the id is kept in the Composio record instead."""
    try:
        await client.delete_trigger(trigger_id)
        return True
    except ComposioError as exc:
        if exc.kind == "not_found":
            return True
    if not remember:
        return False

    def keep(record):
        pending = [p for p in record.get("pending_trigger_deletes", []) if isinstance(p, str)]
        if trigger_id not in pending:
            pending.append(trigger_id)
        record["pending_trigger_deletes"] = pending[-PENDING_MAX:]
    try:
        await _update_record(db, owner_id, keep)
    except AppEventError:
        pass
    return False


async def set_paused(db, owner_id: str, routine: Routine, paused: bool, make_client) -> None:
    """Follow a routine's pause or resume on the remote instance. Best effort; the reason of a failure is shown in the status."""
    row = await _trigger(db, routine.id)
    if row is None or row.state in ("error", "removed"):
        return
    try:
        _, client = await _client(db, owner_id, make_client)
    except AppEventError as exc:
        row.last_error = exc.message[:200]
        await db.commit()
        return
    failure = await _remote_enabled(client, row.composio_trigger_id, not paused)
    if failure is None:
        row.state, row.last_error = ("paused" if paused else "active"), None
    else:
        row.last_error = failure
    await db.commit()


async def remove(db, owner_id: str, routine: Routine, make_client, *, deleting: bool = False) -> None:
    """Switch the wake off (or the routine is being deleted): delete the remote instance, then the local row.
    When Composio cannot be reached the instance id is remembered for a retry; the routine's own deletion is never blocked."""
    row = await _trigger(db, routine.id)
    routine.app_event = None
    if row is None:
        await db.commit()
        return
    trigger_id = row.composio_trigger_id
    try:
        _, client = await _client(db, owner_id, make_client)
    except AppEventError:
        client = None
    # While the routine is being deleted the row goes with it, so the id is remembered in the Composio record;
    # when only the wake is switched off the row stays, as `removed`, until the delete has gone through.
    done = client is not None and await _delete_remote(db, owner_id, client, trigger_id, remember=deleting)
    if not done and not deleting:
        row.state, row.last_error = "removed", "Composio could not be reached, so the remote trigger is not deleted yet. It is retried automatically."
        await db.commit()
        return
    await db.delete(row)
    await db.commit()
    if client is not None:
        await last_trigger_removed_cleanup(db, owner_id, client)


async def mark_error(db, row: AppEventTrigger, reason: str, make_client=None, owner_id: str | None = None) -> None:
    """Stop waking the routine and say why; the routine keeps its schedule. Best effort at Composio when a client is given."""
    row.state, row.last_error = "error", reason[:200]
    await db.commit()
    if make_client is not None and owner_id is not None:
        try:
            _, client = await _client(db, owner_id, make_client)
            await _remote_enabled(client, row.composio_trigger_id, False)
        except AppEventError:
            pass


async def recheck(db, owner_id: str, routine: Routine, make_client=None) -> bool:
    """After an agent, grant or access change: if the routine can no longer read the mail it listens to, mark the trigger
    `error` (the schedule continues). True when it was marked."""
    row = await _trigger(db, routine.id)
    if row is None or row.state in ("error", "removed") or not routine.app_event:
        return False
    try:
        await check(db, owner_id, routine, normalize(routine.app_event))
    except AppEventError as exc:
        await mark_error(db, row, exc.message, make_client, owner_id)
        return True
    return False


async def on_access_changed(db, agent_id: int, make_client=None) -> int:
    """Call when an agent's connected-app access changes: every new-mail trigger of its routines is re-checked."""
    routines = list((await db.execute(select(Routine).where(Routine.agent_id == agent_id, Routine.app_event.is_not(None)))).scalars())
    marked = 0
    for routine in routines:
        marked += await recheck(db, routine.owner_id, routine, make_client)
    return marked


async def apply(db, owner_id: str, routine: Routine, desired: dict | None, make_client) -> None:
    """The router's one entry point: a setting turns the wake on (or changes it), None switches it off."""
    if desired is None:
        await remove(db, owner_id, routine, make_client)
    else:
        await enable(db, owner_id, routine, make_client, desired)


# -- Reconciliation ----------------------------------------------------------------------------------------------------

async def reconcile(db, owner_id: str, make_client) -> dict:
    """Compare the remote instances and webhook with the local rows, at startup and on reconnect. A comparison of two
    lists, never a read of Gmail. Only instances this app made are ever changed; others are counted as `foreign`."""
    report = {"retried_deletes": 0, "recreated": 0, "re_enabled": 0, "disabled": 0, "errors": 0, "foreign": 0}
    rows = list((await db.execute(select(AppEventTrigger).where(AppEventTrigger.owner_id == owner_id))).scalars())
    record = await composio_service.load(db, owner_id)
    pending = [p for p in (record or {}).get("pending_trigger_deletes", []) if isinstance(p, str)]
    if record is None or not (rows or pending or _hook_of(record)):
        return report
    client = make_client(record["api_key"])
    try:
        remote = {t.id: t for t in await client.list_active_triggers(show_disabled=True)}
    except ComposioError:
        report["errors"] += 1
        return report
    still = []
    for trigger_id in pending:
        try:
            await client.delete_trigger(trigger_id)
            report["retried_deletes"] += 1
        except ComposioError as exc:
            if exc.kind == "not_found":
                report["retried_deletes"] += 1
            else:
                still.append(trigger_id)
    if still != pending:
        await _update_record(db, owner_id, lambda r: r.__setitem__("pending_trigger_deletes", still))
    known = {r.composio_trigger_id for r in rows}
    for row in rows:
        if row.state == "removed":
            try:
                await client.delete_trigger(row.composio_trigger_id)
            except ComposioError as exc:
                if exc.kind != "not_found":
                    report["errors"] += 1
                    continue
            await db.delete(row)
            report["retried_deletes"] += 1
            continue
        if row.state == "error":
            continue
        found = remote.get(row.composio_trigger_id)
        if found is None:  # deleted in Composio: make it again, if the account still allows it
            try:
                instance = await client.upsert_trigger(SLUG, row.composio_account_id, {"query": row.query} if row.query else {})
                row.composio_trigger_id, row.last_error = instance.id, None
                report["recreated"] += 1
                if row.state == "paused":
                    await _remote_enabled(client, instance.id, False)
            except ComposioError as exc:
                row.state, row.last_error = "error", (exc.message or "Composio could not recreate the trigger.")[:200]
                report["errors"] += 1
        elif found.disabled and row.state == "active":
            await _remote_enabled(client, row.composio_trigger_id, True)
            report["re_enabled"] += 1
        elif not found.disabled and row.state == "paused":
            await _remote_enabled(client, row.composio_trigger_id, False)
            report["disabled"] += 1
    report["foreign"] = sum(1 for t_id, t in remote.items() if t_id not in known and t_id not in pending and (t.slug or "").upper().startswith("GMAIL"))
    live = any(r.state in ("active", "paused") for r in rows if r.state != "removed")
    await db.commit()
    if live:
        try:
            await _ensure_subscription(db, owner_id, client)
        except (AppEventError, ComposioError):
            report["errors"] += 1
    return report

