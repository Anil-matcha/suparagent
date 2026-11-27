"""The two Gmail label tools of a support worker (BL-173): `gmail_list_labels` and `gmail_apply_worker_label`.

The agent only ever says a thread id and one fixed word (composio_tools.WORKER_LABELS). Everything else is decided
here, on the server:

  * the label's name is built as `Worker/<word>`;
  * its Gmail id is one this app created itself and recorded in `worker_label`. A `Worker/...` label that already
    exists in Gmail but is not in that table was made by someone else: it is refused (`label_collision`), never adopted;
  * the Composio call adds that one id to the thread (GMAIL_MODIFY_THREAD_LABELS, thread level) and sends no removed
    id at all, so no system label (INBOX, UNREAD, TRASH, SPAM...), no foreign label and no label of the owner's can be
    named, changed or removed through it;
  * the BL-172 share of the mailbox is checked before this runs (gmail_share.precheck in composio_service.run_tool),
    so a thread outside the share never reaches Gmail.

Each application is recorded in `worker_label_action` BEFORE the Gmail call, as `outcome_unknown`, and settled to
`applied` or `failed` after it. A crash or a lost answer therefore leaves `outcome_unknown`, which is never repeated
automatically, and the owner's one-click undo (services/worker_labels.py) removes exactly the labels recorded here.
"""
import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import composio_tools
from app.db.models import WorkerLabel, WorkerLabelAction
from app.db.transactions import begin_write

PREFIX = "Worker/"
MAX_APPLICATIONS_PER_TURN = 100
MAX_LABELS_PER_THREAD = 8

COLLISION = {"error": "A Worker/ label with that name already exists in Gmail but this app did not make it, so it is not used. "
                      "Ask the person to rename or delete it, or to use another label.", "code": "label_collision"}


def _limit(message: str) -> dict:
    return {"error": message, "code": "label_limit"}


def label_name(word: str) -> str:
    """`Worker/<word>`; the word has already been checked against composio_tools.WORKER_LABELS."""
    return PREFIX + word


def _id_of(data: object) -> str | None:
    """The label id in a create answer, whichever level of the answer holds it."""
    for node in (data, data.get("response_data") if isinstance(data, dict) else None):
        if isinstance(node, dict) and isinstance(node.get("id"), str) and node["id"]:
            return node["id"]
    return None


def _labels_of(data: object) -> list[dict] | None:
    for node in (data, data.get("response_data") if isinstance(data, dict) else None):
        if isinstance(node, dict) and isinstance(node.get("labels"), list):
            return [item for item in node["labels"] if isinstance(item, dict)]
    return None


async def _gmail_labels(client, session_id: str, account_id: str, execute) -> list[dict] | dict:
    """Every label of the account as Gmail lists them, or an error result."""
    done = await execute(client, session_id, "GMAIL_LIST_LABELS", {"user_id": "me"}, account_id)
    labels = _labels_of(done.data) if done.result_type == "completed" else None
    if labels is None:
        return {"error": "The labels of this Gmail account could not be read.", "code": "app_error"}
    return labels


def _shape(rows: list, not_ours: list[str]) -> dict:
    shown = [{"name": row.name, "id": row.gmail_label_id} for row in rows]
    result = {"notice": composio_tools.NOTICE.format(app="gmail"), "app": "gmail", "count": len(shown),
              "truncated": False, "result": shown}
    if not_ours:
        result["not_ours"] = not_ours
    return result


async def list_labels(db, owner_id: str, client, session_id: str, account_id: str, execute) -> dict:
    """The Worker/ labels this app made in the account, and any look-alike it did not make."""
    rows = list((await db.execute(select(WorkerLabel).where(
        WorkerLabel.owner_id == owner_id, WorkerLabel.account_id == account_id).order_by(WorkerLabel.name))).scalars())
    labels = await _gmail_labels(client, session_id, account_id, execute)
    if isinstance(labels, dict):
        return labels
    ours = {row.name.lower() for row in rows}
    ours_ids = {row.gmail_label_id for row in rows}
    wanted = {label_name(word).lower() for word in composio_tools.WORKER_LABELS}
    foreign = sorted(str(label.get("name")) for label in labels
                     if str(label.get("name", "")).lower() in wanted and str(label.get("name", "")).lower() not in ours
                     and label.get("id") not in ours_ids)
    return _shape(rows, foreign)


async def apply(db, owner_id: str, client, session_id: str, account_id: str, arguments: dict, context: dict | None, execute) -> dict:
    """Add one Worker/ label to one thread, record it, and answer. Raises ComposioError only before anything was sent
    that could have changed the thread; a lost answer is recorded and reported as `outcome_unknown`."""
    from app.services import composio_service

    agent_id = (context or {}).get("agent_id")
    turn_id = (context or {}).get("turn_id")
    run_id = (context or {}).get("run_id")
    if not isinstance(agent_id, int) or not isinstance(turn_id, int):
        return {"error": "This call has no turn to record the label in.", "code": "not_allowed"}
    thread_id, word = arguments["thread_id"], arguments["label"]
    if word not in composio_tools.WORKER_LABELS:  # clean_arguments already refused this; never trust one layer
        return {"error": "That is not one of the label words.", "code": "bad_arguments"}
    name = label_name(word)
    request_key = hashlib.sha256(json.dumps({"account": account_id, "thread": thread_id, "label": name},
                                            sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    # An identical request in this turn or run is answered from the record, never sent again.
    same = (await db.execute(select(WorkerLabelAction).where(
        WorkerLabelAction.agent_id == agent_id, WorkerLabelAction.turn_id == turn_id,
        WorkerLabelAction.request_key == request_key).order_by(WorkerLabelAction.id.desc()))).scalars().first()
    if same is not None and same.status != "undone":
        if same.status == "applied":
            return _applied(thread_id, name, repeated=True)
        if same.status == "outcome_unknown":
            return dict(composio_service.OUTCOME_UNKNOWN)
        return {"error": "That label was tried in this turn and the app refused it. Try again in a later run.",
                "code": same.code or "app_error"}

    made = (await db.scalar(select(func.count()).select_from(WorkerLabelAction).where(
        WorkerLabelAction.agent_id == agent_id, WorkerLabelAction.turn_id == turn_id))) or 0
    if made >= MAX_APPLICATIONS_PER_TURN:
        return _limit(f"At most {MAX_APPLICATIONS_PER_TURN} labels can be added in one run. Continue in the next run.")
    on_thread = set((await db.execute(select(WorkerLabelAction.label_name).where(
        WorkerLabelAction.agent_id == agent_id, WorkerLabelAction.account_id == account_id,
        WorkerLabelAction.thread_id == thread_id,
        WorkerLabelAction.status.in_(("applied", "outcome_unknown"))))).scalars())
    if name not in on_thread and len(on_thread) >= MAX_LABELS_PER_THREAD:
        return _limit(f"A thread can carry at most {MAX_LABELS_PER_THREAD} Worker/ labels.")

    label_id = await _resolve(db, owner_id, client, session_id, account_id, name, execute)
    if isinstance(label_id, dict):
        return label_id

    await begin_write(db)
    reservation = WorkerLabelAction(owner_id=owner_id, agent_id=agent_id, account_id=account_id, thread_id=thread_id,
                                    label_name=name, gmail_label_id=label_id, turn_id=turn_id,
                                    run_id=run_id if isinstance(run_id, int) else None,
                                    request_key=request_key, status="outcome_unknown")
    db.add(reservation)
    await db.flush()
    action_id = reservation.id
    await db.commit()

    async def settle(status: str, code: str | None = None) -> None:
        await begin_write(db)
        row = await db.get(WorkerLabelAction, action_id)
        row.status, row.code = status, code
        await db.commit()

    # The only id sent is the one resolved above, as the only added id; no id is removed.
    body = {"user_id": "me", "thread_id": thread_id, "add_label_ids": [label_id]}
    try:
        done = await execute(client, session_id, "GMAIL_MODIFY_THREAD_LABELS", body, account_id)
    except composio_service.ComposioError as exc:
        if exc.kind in ("unavailable", "bad_response"):
            return dict(composio_service.OUTCOME_UNKNOWN)  # the row stays outcome_unknown and is never repeated
        await settle("failed", "composio_" + exc.kind)
        raise
    if done.result_type == "completed":
        await settle("applied")
        return _applied(thread_id, name)
    await settle("failed", "app_error")
    return {"error": "The app refused or could not do that" + (f": {done.error}" if done.error else "."), "code": "app_error"}


def _applied(thread_id: str, name: str, *, repeated: bool = False) -> dict:
    shown = {"thread_id": thread_id, "label": name, "applied": True}
    if repeated:
        shown["repeated"] = True
    return {"notice": composio_tools.NOTICE.format(app="gmail"), "app": "gmail", "count": 1, "truncated": False, "result": shown}


async def _resolve(db, owner_id: str, client, session_id: str, account_id: str, name: str, execute) -> str | dict:
    """The Gmail id of `name` in this account: the one recorded when this app created it, else a new label this app creates.
    A label with that name that this app did not create is a collision and is refused."""
    row = (await db.execute(select(WorkerLabel).where(
        WorkerLabel.owner_id == owner_id, WorkerLabel.account_id == account_id, WorkerLabel.name == name))).scalars().first()
    labels = None
    if row is not None:
        labels = await _gmail_labels(client, session_id, account_id, execute)
        if isinstance(labels, dict):
            return labels
        if any(label.get("id") == row.gmail_label_id for label in labels):
            return row.gmail_label_id
        # The owner deleted our label in Gmail: forget it and make it again below.
        await begin_write(db)
        await db.delete(row)
        await db.commit()
    if labels is None:
        labels = await _gmail_labels(client, session_id, account_id, execute)
        if isinstance(labels, dict):
            return labels
    if any(str(label.get("name", "")).lower() == name.lower() for label in labels):
        return dict(COLLISION)
    done = await execute(client, session_id, "GMAIL_CREATE_LABEL", {"user_id": "me", "label_name": name}, account_id)
    label_id = _id_of(done.data) if done.result_type == "completed" else None
    if label_id is None:
        return {"error": "The label could not be created in Gmail.", "code": "app_error"}
    await begin_write(db)
    db.add(WorkerLabel(owner_id=owner_id, account_id=account_id, name=name, gmail_label_id=label_id))
    try:
        await db.commit()
    except IntegrityError:  # another call recorded it first
        await db.rollback()
        mine = (await db.execute(select(WorkerLabel).where(
            WorkerLabel.owner_id == owner_id, WorkerLabel.account_id == account_id, WorkerLabel.name == name))).scalars().first()
        if mine is not None:
            return mine.gmail_label_id
    return label_id
