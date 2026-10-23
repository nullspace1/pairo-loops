"""A transcript entry paired with its destination and transient activity identity."""
from dataclasses import dataclass
from typing import Generic, TypeVar
from uuid import UUID
from ..models import TranscriptEntry
MetadataT = TypeVar("MetadataT")
@dataclass(frozen=True, slots=True)
class ActivityIdentity:
    agent_id: UUID
    session_name: str
    family: str
    activity_id: str
@dataclass(frozen=True, slots=True)
class RoutedTranscriptEntry(Generic[MetadataT]):
    agent_id: UUID
    session_name: str
    entry: TranscriptEntry[MetadataT]
    activity: ActivityIdentity | None = None
    @property
    def correlated_entry_id(self) -> UUID | None:
        """Compatibility view; never derived from or persisted with a provider ID."""
        return self.entry.entry_id if self.activity is not None else None
