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


class ActivityTranscriptMetadata(TypedDict):
    """Metadata for a completed provider activity."""

    type: Literal["activity"]
    activity_type: str


class PermissionRequestTranscriptMetadata(TypedDict, total=False):
    """Durable state for a permission request transcript entry."""

    type: Literal["permission_request"]
    request_id: str
    request_type: str
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


class HistoricalTranscriptMetadata(TypedDict):
    """Metadata assigned when a durable entry is reloaded from SQLite."""

    type: Literal["historical"]




TranscriptMetadata: TypeAlias = (
    MessageTranscriptMetadata | ErrorTranscriptMetadata | NetworkTranscriptMetadata
    | ActivityTranscriptMetadata | PermissionRequestTranscriptMetadata
    | PermissionResponseTranscriptMetadata | SystemTranscriptMetadata
    | TokenUsageTranscriptMetadata | HistoricalTranscriptMetadata
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

    @property
    def duration_seconds(self) -> float:
        return (self.last_updated_at - self.created_at).total_seconds()
