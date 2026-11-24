from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ProviderName = Literal["agentmail", "agentphone", "other"]
ResourceKind = Literal["email", "phone"]


class AgentServiceProviderSet(BaseModel):
    provider: ProviderName
    resource_limit: int = Field(default=3, ge=1, le=20)


class AgentServiceProviderOut(BaseModel):
    connection_id: str
    connection_name: str
    provider: ProviderName
    resource_limit: int
    resource_count: int
    created_at: datetime


class AgentServiceResourceCreate(BaseModel):
    connection_id: str = Field(min_length=1, max_length=36)
    kind: ResourceKind
    address: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=100)
    provider_resource_id: str | None = Field(default=None, max_length=200)


class AgentServiceResourceOut(BaseModel):
    id: str
    connection_id: str
    kind: ResourceKind
    address: str
    label: str
    provider_resource_id: str | None
    agent_ids: list[int]
    created_at: datetime


class AgentServiceAssignRequest(BaseModel):
    agent_ids: list[int] = Field(max_length=100)


class AgentServiceAuditOut(BaseModel):
    id: str
    agent_id: int
    agent_name: str
    connection_id: str
    connection_name: str
    provider: str
    tool_name: str
    resources: list[str]
    outcome: Literal["dispatched", "completed", "failed", "outcome_unknown"]
    approved_by: str | None
    created_at: datetime


PresetName = Literal["agentmail", "agentphone"]


class AgentServicePresetOut(BaseModel):
    preset: PresetName
    label: str
    kind: ResourceKind
    endpoint: str
    summary: str


class AgentServiceProviderFromPreset(BaseModel):
    preset: PresetName
    # Defaults to the preset's label.
    name: str | None = Field(default=None, min_length=1, max_length=100)
    resource_limit: int = Field(default=3, ge=1, le=20)


class AgentServiceCandidateOut(BaseModel):
    address: str
    provider_resource_id: str
    label: str
    # Already recorded under this provider.
    added: bool
