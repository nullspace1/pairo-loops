"""Provider-neutral configuration for an agent."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from .mcp.servers import HttpMCPServer, MCPServer, StdioMCPServer
from .permissions import PermissionRequester


JsonObject: TypeAlias = dict[str, JsonValue]


class SandboxMode(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


class ReasoningEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class WebSearchMode(str, Enum):
    DISABLED = "disabled"
    CACHED = "cached"
    LIVE = "live"


class ApprovalMode(str, Enum):
    AUTO = "auto"
    USER = "user"


class HookType(str, Enum):
    PRE_TOOL_USE = "PreToolUse"
    PERMISSION_REQUEST = "PermissionRequest"
    POST_TOOL_USE = "PostToolUse"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    STOP = "Stop"
    INTERRUPT = "Interrupt"


class Hook(BaseModel):
    """A shell command executed by Codex for a configured lifecycle event."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    command: str
    is_async: bool = Field(alias="async")

    @field_validator("command")
    @classmethod
    def require_command(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Hook command cannot be empty.")
        return normalized


class AgentConfiguration(BaseModel):
    """Immutable, provider-neutral launch configuration."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        strict=True,
    )

    sandbox: SandboxMode
    workspace: Path
    model: str
    base_instructions: str | None = None
    developer_instructions: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    web_search: WebSearchMode | None = None
    approval_mode: ApprovalMode = ApprovalMode.AUTO
    permission_requester: PermissionRequester | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )
    mcp_servers: list[MCPServer] = []
    skill_folder: Path | None = None
    hooks: dict[HookType, list[Hook]] = Field(default_factory=dict)

    @field_validator("workspace", mode="before")
    @classmethod
    def normalize_workspace(cls, value: str | Path) -> Path:
        if isinstance(value, (str, Path)):
            return Path(value).resolve()
        raise ValueError("workspace must be a string or pathlib.Path.")

    @field_validator("skill_folder", mode="before")
    @classmethod
    def normalize_skill_folder(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        if not isinstance(value, (str, Path)):
            raise ValueError("skill_folder must be a string or pathlib.Path.")
        folder = Path(value).resolve()
        if not folder.is_dir():
            raise ValueError("skill_folder must be an existing directory.")
        return folder

    @field_validator("model")
    @classmethod
    def require_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model cannot be empty.")
        return normalized

    @field_validator("mcp_servers")
    @classmethod
    def unique_mcp_names(cls, servers: tuple[MCPServer, ...]) -> tuple[MCPServer, ...]:
        names = [server.name for server in servers]
        if len(names) != len(set(names)):
            raise ValueError("MCP server names must be unique.")
        return servers

    @model_validator(mode="after")
    def require_interactive_requester(self) -> "AgentConfiguration":
        if self.approval_mode is ApprovalMode.USER and self.permission_requester is None:
            raise ValueError("User approval mode requires a permission requester.")
        return self


Sandbox = SandboxMode

__all__ = [
    "AgentConfiguration", "ApprovalMode", "Hook", "HookType", "HttpMCPServer", "JsonObject",
    "JsonValue", "MCPServer", "ReasoningEffort", "Sandbox", "SandboxMode",
    "StdioMCPServer", "WebSearchMode",
]
