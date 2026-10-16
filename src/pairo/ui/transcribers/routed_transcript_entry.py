"""A transcript entry paired with its destination."""

from dataclasses import dataclass
from typing import Generic, TypeVar
from uuid import UUID

from ..models import TranscriptEntry


MetadataT = TypeVar("MetadataT")


@dataclass(frozen=True, slots=True)
class RoutedTranscriptEntry(Generic[MetadataT]):
    agent_id: UUID
    session_name: str
    entry: TranscriptEntry[MetadataT]

