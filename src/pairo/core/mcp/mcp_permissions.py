"""Provider-neutral MCP tool permission configuration."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MCPToolApprovalMode(str, Enum):
    AUTO = "auto"
    PROMPT = "prompt"
    WRITES = "writes"
    APPROVE = "approve"


class MCPPermissions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    default_tools_approval_mode: MCPToolApprovalMode | None = MCPToolApprovalMode.APPROVE
    tool_approval_modes: dict[str, MCPToolApprovalMode] = Field(default_factory=dict)

    @field_validator("tool_approval_modes")
    @classmethod
    def validate_tool_names(
        cls, modes: dict[str, MCPToolApprovalMode]
    ) -> dict[str, MCPToolApprovalMode]:
        normalized: dict[str, MCPToolApprovalMode] = {}
        for name, mode in modes.items():
            name = name.strip()
            if not name:
                raise ValueError("MCP tool permission entries require a tool name.")
            normalized[name] = mode
        return normalized


__all__ = ["MCPPermissions", "MCPToolApprovalMode"]

