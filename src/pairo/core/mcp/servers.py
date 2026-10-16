"""Provider-neutral configuration models for MCP server connections."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .mcp_permissions import MCPPermissions


class HttpMCPServer(BaseModel):
    """A streamable-HTTP MCP server with environment-backed headers.

    ``env`` maps HTTP header names to environment-variable names resolved by Codex.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    transport: Literal["http"] = "http"
    name: str
    url: str
    env: dict[str, str] = Field(default_factory=dict)
    startup_timeout_sec: float = Field(default=10.0, gt=0)
    permissions: MCPPermissions | None = MCPPermissions()

    @field_validator("name", "url")
    @classmethod
    def require_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("MCP server name and URL cannot be empty.")
        return normalized

    @property
    def description(self) -> str:
        return self.url


class StdioMCPServer(BaseModel):
    """An MCP server launched as a local process."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    transport: Literal["stdio"] = "stdio"
    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    startup_timeout_sec: float = Field(default=10.0, gt=0)
    permissions: MCPPermissions | None = None

    @field_validator("name", "command")
    @classmethod
    def require_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("MCP server name and command cannot be empty.")
        return normalized

    @property
    def description(self) -> str:
        return " ".join((self.command, *self.args))


MCPServer: TypeAlias = Annotated[
    Union[HttpMCPServer, StdioMCPServer],
    Field(discriminator="transport"),
]


__all__ = ["HttpMCPServer", "MCPServer", "StdioMCPServer"]

