from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr

AuthMode = Literal["none", "bearer", "header"]
ConnectionState = Literal["not_tested", "connected", "unreachable", "needs_sign_in", "disabled"]


class CustomMcpCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=300)
    auth_mode: AuthMode = "none"
    auth_header_name: str | None = Field(default=None, max_length=64)
    # Write-only. Never returned by any route.
    credential: SecretStr | None = None


class CustomMcpUpdate(BaseModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    endpoint: str | None = Field(default=None, min_length=1, max_length=300)
    auth_mode: AuthMode | None = None
    auth_header_name: str | None = Field(default=None, max_length=64)
    # Omitted or blank keeps the saved secret; use DELETE .../credential to remove it.
    credential: SecretStr | None = None


class CustomMcpRevision(BaseModel):
    revision: int = Field(ge=1)


class CustomMcpConnectionOut(BaseModel):
    id: str
    name: str
    transport: str
    endpoint: str
    auth_mode: AuthMode
    auth_header_name: str | None
    enabled: bool
    revision: int
    state: ConnectionState
    last_error_code: str | None
    credential_configured: bool
    tool_count: int | None
    last_tested_at: datetime | None


class CustomMcpToolOut(BaseModel):
    name: str
    description: str
    input_schema: dict
    schema_digest: str


class CustomMcpGrantSelection(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    schema_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class CustomMcpGrantRequest(BaseModel):
    revision: int = Field(ge=1)
    tools: list[CustomMcpGrantSelection] = Field(max_length=100)
