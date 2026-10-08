"""Provider-neutral interactive permission contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, ConfigDict, JsonValue


JsonObject = dict[str, JsonValue]


class PermissionRequestType(str, Enum):
    COMMAND_EXECUTION = "command execution"
    FILE_CHANGE = "file change"
    ADDITIONAL_PERMISSIONS = "additional permissions"


class PermissionDecision(str, Enum):
    ACCEPT = "accept"
    ACCEPT_FOR_SESSION = "acceptForSession"
    DECLINE = "decline"


class ApprovalRequest(BaseModel):
    """A provider request associated with an opaque session identifier."""

    model_config = ConfigDict(frozen=True)

    session_identifier: JsonObject
    request_type: PermissionRequestType
    details: JsonObject


class PermissionRequester(ABC):
    @abstractmethod
    async def request(self, request: ApprovalRequest) -> PermissionDecision:
        """Ask the host application to decide a translated approval request."""


__all__ = [
    "ApprovalRequest",
    "PermissionDecision",
    "PermissionRequester",
    "PermissionRequestType",
]
