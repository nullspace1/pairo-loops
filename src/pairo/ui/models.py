"""UI-owned session and transcript models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Generic, Literal, TypeAlias, TypedDict, TypeVar
from uuid import UUID, uuid4

from pairo.core.agent import JsonObject


class EventDetailLevel(str, Enum):
    NORMAL = "normal"
    DEBUG = "debug"


class MessageTranscriptMetadata(TypedDict):
    """Metadata for a message emitted by a person or an agent."""

    type: Literal["message"]


class ErrorTranscriptMetadata(TypedDict):
    """Metadata for an agent error."""

    type: Literal["error"]


class NetworkTranscriptMetadata(TypedDict):
    """Metadata for an inter-agent network message."""

    type: Literal["network"]


class CommandExecutionTranscriptMetadata(TypedDict):
    type: Literal["command_execution"]


class McpToolCallTranscriptMetadata(TypedDict):
    type: Literal["mcp_tool_call"]


class PermissionRequestTranscriptMetadata(TypedDict, total=False):
    """Durable state for a permission request transcript entry."""

    type: Literal["permission_request"]
    request_id: str
    request: JsonObject
    status: Literal["pending", "resolved"]
    decision: str


class PermissionResponseTranscriptMetadata(TypedDict):
    """Metadata for the decision recorded for a permission request."""

    type: Literal["permission_response"]
    request_id: str
    decision: str


class SystemTranscriptMetadata(TypedDict):
    """Metadata for UI and command status messages."""

    type: Literal["system"]


class TokenUsageTranscriptMetadata(TypedDict):
    """Metadata for provider token accounting snapshots."""

    type: Literal["token_usage"]


class SessionTableRow(TypedDict):
    """JSON-safe session values rendered by the sessions transcript entry."""

    session_name: str
    current: bool
    created_at: str
    last_message_at: str | None
    last_updated_at: str


class SessionTableTranscriptMetadata(TypedDict):
    """Durable table model emitted by the ``/sessions`` command."""

    type: Literal["session_table"]
    sessions: list[SessionTableRow]


class HistoricalTranscriptMetadata(TypedDict):
    """Metadata assigned when a durable entry is reloaded from SQLite."""

    type: Literal["historical"]




TranscriptMetadata: TypeAlias = (
    MessageTranscriptMetadata | ErrorTranscriptMetadata | NetworkTranscriptMetadata
    | CommandExecutionTranscriptMetadata
    | McpToolCallTranscriptMetadata | PermissionRequestTranscriptMetadata
    | PermissionResponseTranscriptMetadata | SystemTranscriptMetadata
    | TokenUsageTranscriptMetadata | SessionTableTranscriptMetadata
    | HistoricalTranscriptMetadata
)


MetadataT = TypeVar("MetadataT", covariant=True)


@dataclass(frozen=True, slots=True)
class TranscriptEntry(Generic[MetadataT]):
    role: str
    content: str
    detail_level: EventDetailLevel
    metadata: MetadataT
    entry_id: UUID = field(default_factory=uuid4, compare=False)


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    agent_id: UUID
    session_name: str
    identifier: JsonObject
    created_at: datetime
    last_message_at: datetime | None
    last_updated_at: datetime
