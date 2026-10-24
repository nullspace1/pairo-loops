"""Declarative, provider-neutral agent configuration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from pairo.core.agent.approval_mode import ApprovalMode
from pairo.core.agent.hook import Hook
from pairo.core.agent.hook_type import HookType
from pairo.core.agent.reasoning_effort import ReasoningEffort
from pairo.core.agent.sandbox_permission import SandboxPermission
from pairo.core.agent.web_search_mode import WebSearchMode
from pairo.core.mcp.servers import MCPServer
from pairo.core.permissions.permission_requester import PermissionRequester


class AgentConfiguration(BaseModel):
    """Agent identity and configuration.

    The model is externally frozen so configuration cannot be changed without
    validation. Setter methods validate a complete replacement model before
    committing a change, which keeps failed updates atomic.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        strict=True,
    )

    id: UUID = Field(default_factory=uuid4, frozen=True)
    name: str = Field(frozen=True)
    description: str = ""
    sandbox: SandboxPermission
    workspace: Path
    model: str
    base_instructions: str | None = None
    developer_instructions: str | None = None
    reasoning_effort: ReasoningEffort = ReasoningEffort.LOW
    web_search: WebSearchMode = WebSearchMode.CACHED
    approval_mode: ApprovalMode = ApprovalMode.AUTO
    permission_requester: PermissionRequester | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )
    mcp_servers: tuple[MCPServer, ...] = ()
    skill_folders: tuple[Path, ...] = ()
    hooks: Mapping[HookType, tuple[Hook, ...]] = Field(default_factory=dict)
    folder_permissions: Mapping[Path, SandboxPermission] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def require_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Agent name cannot be empty.")
        return normalized

    @field_validator("model")
    @classmethod
    def require_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model cannot be empty.")
        return normalized

    @field_validator("workspace", mode="before")
    @classmethod
    def validate_workspace(cls, value: str | Path) -> Path:
        workspace = Path(value).resolve()
        if not workspace.is_dir():
            raise ValueError("workspace must be an existing directory.")
        return workspace

    @field_validator("skill_folders", mode="before")
    @classmethod
    def validate_skill_folders(
        cls,
        values: Sequence[str | Path],
    ) -> tuple[Path, ...]:
        folders = tuple(Path(value).resolve() for value in values)
        if any(not folder.is_dir() for folder in folders):
            raise ValueError("skill folders must be existing directories.")
        return folders

    @field_validator("mcp_servers", mode="before")
    @classmethod
    def normalize_mcp_servers(cls, servers: Sequence[MCPServer]) -> tuple[MCPServer, ...]:
        return tuple(servers)

    @field_validator("mcp_servers")
    @classmethod
    def require_unique_mcp_names(
        cls,
        servers: tuple[MCPServer, ...],
    ) -> tuple[MCPServer, ...]:
        names = [server.name for server in servers]
        if len(names) != len(set(names)):
            raise ValueError("MCP server names must be unique.")
        return servers

    @field_validator("hooks", mode="before")
    @classmethod
    def normalize_hooks(
        cls,
        hooks: Mapping[HookType, Sequence[Hook]],
    ) -> Mapping[HookType, tuple[Hook, ...]]:
        return MappingProxyType(
            {hook_type: tuple(configured_hooks) for hook_type, configured_hooks in hooks.items()}
        )

    @field_validator("folder_permissions", mode="before")
    @classmethod
    def normalize_folder_permissions(
        cls,
        permissions: Mapping[Path, SandboxPermission],
    ) -> Mapping[Path, SandboxPermission]:
        normalized = {Path(folder).resolve(): permission for folder, permission in permissions.items()}
        if any(not folder.is_dir() for folder in normalized):
            raise ValueError("permission folders must be existing directories.")
        return MappingProxyType(normalized)

    @model_validator(mode="after")
    def require_interactive_requester(self) -> "AgentConfiguration":
        if self.approval_mode is ApprovalMode.USER and self.permission_requester is None:
            raise ValueError("User approval mode requires a permission requester.")
        # Pydantic materializes abstract mappings as dictionaries. Re-wrap the
        # validated value so plugins must use the mutation API and cannot
        # bypass cross-field validation with an in-place edit.
        object.__setattr__(self, "hooks", MappingProxyType(dict(self.hooks)))
        object.__setattr__(
            self,
            "folder_permissions",
            MappingProxyType(dict(self.folder_permissions)),
        )
        return self

    @field_serializer("hooks")
    def serialize_hooks(
        self,
        hooks: Mapping[HookType, tuple[Hook, ...]],
    ) -> dict[HookType, tuple[Hook, ...]]:
        return dict(hooks)

    @field_serializer("folder_permissions")
    def serialize_folder_permissions(
        self,
        permissions: Mapping[Path, SandboxPermission],
    ) -> dict[Path, SandboxPermission]:
        return dict(permissions)

    def set_description(self, description: str) -> None:
        self._replace(description=description)

    def set_sandbox(self, sandbox: SandboxPermission) -> None:
        self._replace(sandbox=sandbox)

    def set_folder_permissions(
        self,
        permissions: Mapping[Path, SandboxPermission],
    ) -> None:
        self._replace(folder_permissions=permissions)

    def add_folder_permission(
        self,
        folder: str | Path,
        permission: SandboxPermission,
    ) -> None:
        configured = dict(self.folder_permissions)
        configured[Path(folder).resolve()] = permission
        self.set_folder_permissions(configured)

    def remove_folder_permission(self, folder: str | Path) -> None:
        target = Path(folder).resolve()
        if target not in self.folder_permissions:
            raise KeyError(f"Folder permission '{target}' is not configured.")
        configured = dict(self.folder_permissions)
        configured.pop(target)
        self.set_folder_permissions(configured)

    def set_workspace(self, workspace: str | Path) -> None:
        self._replace(workspace=workspace)

    def set_model(self, model: str) -> None:
        self._replace(model=model)

    def set_base_instructions(self, instructions: str | None) -> None:
        self._replace(base_instructions=instructions)

    def append_base_instructions(self, instructions: str) -> None:
        """Append a distinct block to the base instructions."""
        self.set_base_instructions(self._appended_instructions(self.base_instructions, instructions))

    def set_developer_instructions(self, instructions: str | None) -> None:
        self._replace(developer_instructions=instructions)

    def append_developer_instructions(self, instructions: str) -> None:
        """Append a distinct block to the developer instructions."""
        self.set_developer_instructions(
            self._appended_instructions(self.developer_instructions, instructions)
        )

    def set_reasoning_effort(self, effort: ReasoningEffort) -> None:
        self._replace(reasoning_effort=effort)

    def set_web_search(self, mode: WebSearchMode) -> None:
        self._replace(web_search=mode)

    def set_approval_mode(self, mode: ApprovalMode) -> None:
        self._replace(approval_mode=mode)

    def set_permission_requester(
        self,
        requester: PermissionRequester | None,
    ) -> None:
        self._replace(permission_requester=requester)

    def set_mcp_servers(self, servers: Sequence[MCPServer]) -> None:
        self._replace(mcp_servers=tuple(servers))

    def add_mcp_server(self, server: MCPServer) -> None:
        self.set_mcp_servers((*self.mcp_servers, server))

    def remove_mcp_server(self, name: str) -> None:
        remaining = tuple(server for server in self.mcp_servers if server.name != name)
        if len(remaining) == len(self.mcp_servers):
            raise KeyError(f"MCP server '{name}' is not configured.")
        self.set_mcp_servers(remaining)

    def set_skill_folders(self, folders: Sequence[str | Path]) -> None:
        self._replace(skill_folders=tuple(folders))

    def add_skill_folder(self, folder: str | Path) -> None:
        self.set_skill_folders((*self.skill_folders, folder))

    def remove_skill_folder(self, folder: str | Path) -> None:
        target = Path(folder).resolve()
        remaining = tuple(value for value in self.skill_folders if value != target)
        if len(remaining) == len(self.skill_folders):
            raise KeyError(f"Skill folder '{target}' is not configured.")
        self.set_skill_folders(remaining)

    def set_hooks(self, hooks: Mapping[HookType, Sequence[Hook]]) -> None:
        self._replace(hooks=hooks)

    def add_hook(self, hook_type: HookType, hook: Hook) -> None:
        configured = {key: tuple(value) for key, value in self.hooks.items()}
        configured[hook_type] = (*configured.get(hook_type, ()), hook)
        self.set_hooks(configured)

    def remove_hook(self, hook_type: HookType, hook: Hook) -> None:
        configured = {key: tuple(value) for key, value in self.hooks.items()}
        existing = configured.get(hook_type, ())
        if hook not in existing:
            raise KeyError(f"Hook is not configured for '{hook_type.value}'.")
        remaining = tuple(value for value in existing if value != hook)
        if remaining:
            configured[hook_type] = remaining
        else:
            configured.pop(hook_type, None)
        self.set_hooks(configured)

    def snapshot(self) -> "AgentConfiguration":
        """Return an independent configuration snapshot for a provider session."""
        return type(self).model_validate(self._field_values())

    def _replace(self, **changes: Any) -> None:
        values = self._field_values()
        values.update(changes)
        candidate = type(self).model_validate(values)
        for field_name in changes:
            object.__setattr__(self, field_name, getattr(candidate, field_name))

    def _field_values(self) -> dict[str, object]:
        return {
            field_name: getattr(self, field_name)
            for field_name in type(self).model_fields
        }

    @staticmethod
    def _appended_instructions(current: str | None, addition: str) -> str | None:
        normalized = addition.strip()
        if not normalized:
            return current
        if not current:
            return normalized
        return f"{current.rstrip()}\n\n{normalized}"


__all__ = ["AgentConfiguration"]

