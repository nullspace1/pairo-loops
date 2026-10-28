"""Events that describe durable transcript mutations."""

from uuid import UUID, uuid4

from pydantic import Field

from pydantic import BaseModel

from .models import TranscriptEntry, TranscriptMetadata


class TranscriptRequest(BaseModel):
    """A pipeline request for a durable transcript mutation."""

    agent_id: UUID
    session_name: str
    request_id: UUID = Field(default_factory=uuid4)


class AddTranscriptEntry(TranscriptRequest):
    entry: TranscriptEntry[TranscriptMetadata]


class UpdateTranscriptEntry(TranscriptRequest):
    entry: TranscriptEntry[TranscriptMetadata]


class DeleteTranscriptEntry(TranscriptRequest):
    entry_id: UUID


TranscriptMutation = AddTranscriptEntry | UpdateTranscriptEntry | DeleteTranscriptEntry


__all__ = ["AddTranscriptEntry", "UpdateTranscriptEntry", "DeleteTranscriptEntry", "TranscriptMutation"]
