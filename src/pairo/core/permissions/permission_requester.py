"""Provider-neutral interactive permission contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue


JsonObject = dict[str, JsonValue]


class PermissionRequestType(str, Enum):
    COMMAND_EXECUTION = "command execution"
    FILE_CHANGE = "file change"
    ADDITIONAL_PERMISSIONS = "additional permissions"


class PermissionDecision(str, Enum):
    ACCEPT = "accept"
    ACCEPT_FOR_SESSION = "acceptForSession"
    DECLINE = "decline"


class _ApprovalRequest(BaseModel):
    """Immutable common correlation data for a provider approval request."""

    model_config = ConfigDict(frozen=True)

    session_identifier: JsonObject

    def history_lines(self) -> tuple[str, ...]:
        """Return durable, decision-essential text for transcript history."""
        raise NotImplementedError

    def decision_response(self, decision: PermissionDecision) -> JsonObject:
        """Return the provider response semantics shared by ordinary approvals."""
        return {"decision": decision.value}


class CommandExecutionApprovalRequest(_ApprovalRequest):
    """Approval data essential to deciding whether a command may run."""

    request_type: Literal[PermissionRequestType.COMMAND_EXECUTION] = (
        PermissionRequestType.COMMAND_EXECUTION
    )
    command: str = ""
    working_directory: str | None = None
    rationale: str | None = None

    def history_lines(self) -> tuple[str, ...]:
        lines = ["Allow this command to run?", "Command:", self.command or "Not provided"]
        if self.working_directory:
            lines.append(f"Working directory: {self.working_directory}")
        if self.rationale:
            lines.append(f"Why this is needed: {self.rationale}")
        return tuple(lines)


class FileChangeApprovalRequest(_ApprovalRequest):
    """Approval data essential to deciding whether files may be changed."""

    request_type: Literal[PermissionRequestType.FILE_CHANGE] = PermissionRequestType.FILE_CHANGE
    affected_paths: tuple[str, ...] = ()
    change_summary: str = ""
    rationale: str | None = None

    def history_lines(self) -> tuple[str, ...]:
        lines = ["Allow these file changes?"]
        if self.affected_paths:
            lines.append("Affected files:")
            lines.extend(f"- {path}" for path in self.affected_paths)
        if self.change_summary:
            lines.append(f"Changes: {self.change_summary}")
        if self.rationale:
            lines.append(f"Why this is needed: {self.rationale}")
        if len(lines) == 1:
            lines.append("The agent did not provide file-change details.")
        return tuple(lines)


class AdditionalPermissionsApprovalRequest(_ApprovalRequest):
    """Approval data for an extension to the provider permission set."""

    request_type: Literal[PermissionRequestType.ADDITIONAL_PERMISSIONS] = (
        PermissionRequestType.ADDITIONAL_PERMISSIONS
    )
    requested_permissions: JsonObject = Field(default_factory=dict)
    rationale: str | None = None

    def history_lines(self) -> tuple[str, ...]:
        lines = ["Allow these additional permissions?"]
        if self.requested_permissions:
            lines.append("Requested permissions:")
            lines.extend(f"- {name}" for name in self.requested_permissions)
        if self.rationale:
            lines.append(f"Why this is needed: {self.rationale}")
        if len(lines) == 1:
            lines.append("The agent did not provide additional permission details.")
        return tuple(lines)

    def decision_response(self, decision: PermissionDecision) -> JsonObject:
        if decision is PermissionDecision.DECLINE:
            return {"permissions": {}}
        response: JsonObject = {"permissions": self.requested_permissions}
        if decision is PermissionDecision.ACCEPT_FOR_SESSION:
            response["scope"] = "session"
        return response


ApprovalRequest: TypeAlias = Annotated[
    CommandExecutionApprovalRequest
    | FileChangeApprovalRequest
    | AdditionalPermissionsApprovalRequest,
    Field(discriminator="request_type"),
]


class PermissionRequester(ABC):
    @abstractmethod
    async def request(self, request: ApprovalRequest) -> PermissionDecision:
        """Decide a translated provider approval request asynchronously."""


__all__ = [
    "AdditionalPermissionsApprovalRequest",
    "ApprovalRequest",
    "CommandExecutionApprovalRequest",
    "FileChangeApprovalRequest",
    "PermissionDecision",
    "PermissionRequester",
    "PermissionRequestType",
]
