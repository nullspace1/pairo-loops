"""Events that describe durable transcript mutations."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .models import TranscriptEntry


class TranscriptEntryAdded(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_id: UUID
    session_name: str
    entry: TranscriptEntry


class TranscriptEntryUpdated(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_id: UUID
    session_name: str
    entry: TranscriptEntry


class TranscriptEntryDeleted(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_id: UUID
    session_name: str
    entry_id: UUID


TranscriptMutation = TranscriptEntryAdded | TranscriptEntryUpdated | TranscriptEntryDeleted


__all__ = [
    "TranscriptEntryAdded",
    "TranscriptEntryDeleted",
    "TranscriptEntryUpdated",
    "TranscriptMutation",
]
