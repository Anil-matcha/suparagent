from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ScheduleKind = Literal["cron", "weekdays", "interval", "once", "manual"]
ApprovalMode = Literal["ask", "accept_edits", "approve_for_me", "plan", "full_access"]


class ConnectedAppGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    account_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("tool")
    @classmethod
    def routine_tool(cls, value):
        from app.composio_tools import BY_NAME, ROUTINE_WRITE_TOOLS
        tool = BY_NAME.get(value)
        if tool is None or (tool.kind != "read" and value not in ROUTINE_WRITE_TOOLS):
            raise ValueError("Select a read action or a supported routine write action")
        return value


class AppEventQuiet(BaseModel):
    """Hours during which new mail does not start a run; the mail waits for the end of the window."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    to: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @field_validator("to")
    @classmethod
    def different(cls, value, info):
        if info.data.get("from_") == value:
            raise ValueError("Quiet hours need two different times.")
        return value


class AppEventConfig(BaseModel):
    """A routine's optional new-mail trigger (BL-177): wake the routine when mail arrives in one Gmail account."""
    model_config = ConfigDict(extra="forbid")
    app: Literal["gmail"] = "gmail"
    account_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    # The owner's Gmail search (for example `label:support`), applied at the source so other mail never produces an event.
    query: str = Field(default="", max_length=200)
    debounce_minutes: int = Field(default=5, ge=1, le=60)
    max_runs_per_day: int = Field(default=12, ge=1, le=48)
    quiet: AppEventQuiet | None = None

    @field_validator("query")
    @classmethod
    def plain_query(cls, value):
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("The mail search must be plain text.")
        return value.strip()

    def stored(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True)


class AppEventStatus(BaseModel):
    """What the routine editor shows about the new-mail trigger. Counts only; never an id, a sender or text."""
    state: str = "off"  # off, active, paused or error
    last_event_at: datetime | None = None
    last_error: str | None = None
    today: dict[str, int] = Field(default_factory=dict)
    can_receive: bool = False
    reason: str | None = None   # why this install cannot receive events, as a code
    message: str | None = None  # the same, in plain words


def validate_draft_grants(grants: list[ConnectedAppGrant]) -> None:
    """A checked reply workflow has exactly one mailbox and no other writes."""
    draft = "gmail_prepare_checked_reply"
    if not any(g.tool == draft for g in grants):
        return
    from app.composio_tools import BY_NAME
    tools = {g.tool for g in grants}
    if (len({g.account_id for g in grants}) != 1
            or not {"gmail_fetch_message", "gmail_get_profile"}.issubset(tools)
            or any(g.tool != draft and (g.tool not in BY_NAME or BY_NAME[g.tool].app != "gmail" or BY_NAME[g.tool].kind != "read") for g in grants)):
        raise ValueError("A checked reply requires Read a message and View account profile from one Gmail account, with no other writes or apps.")


def is_shadow(grants) -> bool:
    """Whether grants make a routine a shadow run (BL-173): it records how it ranks mail or adds its own labels."""
    from app.composio_tools import LABEL_APPLY, TRIAGE_MARK
    return any(g.tool in (TRIAGE_MARK, LABEL_APPLY) for g in grants)


def validate_shadow_grants(grants: list[ConnectedAppGrant]) -> None:
    """A shadow run works in one Gmail account and only reads mail, records how it ranks it, and adds labels under
    Worker/. A routine holding `gmail_triage_mark` or `gmail_apply_worker_label` therefore holds no other app, no
    sending, no drafting and no other change (a change tool of any kind except the label tool). Exact grants are all
    a routine ever gets, so anything not named here is refused at dispatch too (composio_bridge.execute_routine)."""
    if not is_shadow(grants):
        return
    from app.composio_tools import BY_NAME, LABEL_APPLY
    if (len({g.account_id for g in grants}) != 1
            or any(g.tool not in BY_NAME or BY_NAME[g.tool].app != "gmail"
                   or (BY_NAME[g.tool].kind != "read" and g.tool != LABEL_APPLY) for g in grants)):
        raise ValueError("A shadow run uses one Gmail account and only reads mail, records how it ranks it and adds its own "
                         "labels under Worker/: no other app, and no sending, drafting or other changes.")


class RoutineCreateRequest(BaseModel):
    agent_id: int
    uninterrupted_access: bool = False
    connected_app_grants: list[ConnectedAppGrant] = Field(default_factory=list, max_length=50)
    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=20_000)
    schedule_kind: ScheduleKind = "manual"
    schedule_spec: dict = Field(default_factory=dict)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    paused: bool = False
    run_limit_minutes: int = 30
    approval_mode: ApprovalMode = "ask"
    approval_timeout_minutes: int = 30
    catch_up_minutes: int = 60
    quiet_reporting: bool = False
    retry_limit: int = 0
    retry_delay_minutes: int = 5
    app_event: AppEventConfig | None = None


class RoutineUpdateRequest(BaseModel):
    uninterrupted_access: bool | None = None
    connected_app_grants: list[ConnectedAppGrant] | None = Field(default=None, max_length=50)
    agent_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=80)
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    schedule_kind: ScheduleKind | None = None
    schedule_spec: dict | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    run_limit_minutes: int | None = None
    approval_mode: ApprovalMode | None = None
    approval_timeout_minutes: int | None = None
    catch_up_minutes: int | None = None
    quiet_reporting: bool | None = None
    retry_limit: int | None = None
    retry_delay_minutes: int | None = None
    # Present and null switches the new-mail wake off; absent leaves it alone.
    app_event: AppEventConfig | None = None


class SchedulePreviewRequest(BaseModel):
    schedule_kind: ScheduleKind
    schedule_spec: dict = Field(default_factory=dict)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)


class SchedulePreviewResponse(BaseModel):
    summary: str
    next_dates: list[datetime]


class RoutineAppActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tool: str
    account_id: str
    status: str
    code: str | None
    result: dict | None
    created_at: datetime


class RoutineRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    routine_id: int
    uninterrupted_access: bool = False
    connected_app_grants: list[ConnectedAppGrant] = Field(default_factory=list)
    trigger: str
    state: str
    scheduled_for: datetime | None
    thread_id: int | None
    turn_id: int | None
    outcome: str | None = None
    outcome_details: dict | None = None
    result: str | None
    error: str | None
    waiting_since: datetime | None
    waited_seconds: int
    attempt: int = 1
    retry_of: int | None = None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class RoutineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    uninterrupted_access: bool = False
    connected_app_grants: list[ConnectedAppGrant] = Field(default_factory=list)
    name: str
    prompt: str
    schedule_kind: str
    schedule_spec: dict
    timezone: str
    paused: bool
    paused_reason: str | None
    next_run_at: datetime | None
    run_limit_minutes: int
    approval_mode: str
    approval_timeout_minutes: int
    catch_up_minutes: int
    quiet_reporting: bool = False
    retry_limit: int = 0
    retry_delay_minutes: int = 5
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Derived for the client.
    summary: str = ""
    status: Literal["on", "off", "done"] = "on"
    next_dates: list[datetime] = Field(default_factory=list)
    webhook_enabled: bool = False
    latest_run: RoutineRunResponse | None = None
    # The new-mail trigger (BL-177): its setting (the account id is the only id) and how it is doing.
    app_event: dict | None = None
    app_event_status: AppEventStatus | None = None


class WebhookSecretResponse(BaseModel):
    routine: RoutineResponse
    # Shown once: only its hash is stored.
    secret: str
    path: str


class RoutineImportRequest(BaseModel):
    agent_id: int
    documents: list[dict] = Field(min_length=1, max_length=50)


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    title: str
    body: str | None
    routine_id: int | None
    run_id: int | None
    thread_id: int | None
    read_at: datetime | None
    created_at: datetime


class UnreadCountResponse(BaseModel):
    unread: int


class WaitingApproval(BaseModel):
    id: str
    tool: str
    summary: str | None = None
    kind: str | None = None
    input: dict = Field(default_factory=dict)
    created_at: datetime


class WaitingRoutineRun(BaseModel):
    """A routine run paused on a question, for the agent's own chat. Answers go
    through the existing thread approval route, using `thread_id`."""

    run_id: int
    routine_id: int
    routine_name: str
    thread_id: int
    waiting_since: datetime | None
    approval_timeout_minutes: int
    approvals: list[WaitingApproval]
