"""Explicit durable transcript operations followed by transient notifications."""

from uuid import UUID

from pairo.core.events import EventPipeline

from .models import TranscriptEntry, TranscriptMetadata
from .session_repository import SessionRepository
from .transcript_events import (
    TranscriptEntryAdded,
    TranscriptEntryDeleted,
    TranscriptEntryUpdated,
)


class TranscriptService:
    """Persist transcript mutations before notifying interested UI consumers."""

    def __init__(
        self,
        repository: SessionRepository,
        event_pipeline: EventPipeline,
    ) -> None:
        self._repository = repository
        self._event_pipeline = event_pipeline

    async def add(
        self,
        agent_id: UUID,
        session_name: str,
        entry: TranscriptEntry[TranscriptMetadata],
    ) -> None:
        await self._repository.add_entry(agent_id, session_name, entry)
        self._event_pipeline.push(
            TranscriptEntryAdded(
                agent_id=agent_id,
                session_name=session_name,
                entry=entry,
            )
        )

    async def update(
        self,
        agent_id: UUID,
        session_name: str,
        entry: TranscriptEntry[TranscriptMetadata],
    ) -> None:
        await self._repository.update_entry(agent_id, session_name, entry)
        self._event_pipeline.push(
            TranscriptEntryUpdated(
                agent_id=agent_id,
                session_name=session_name,
                entry=entry,
            )
        )

    async def delete(
        self,
        agent_id: UUID,
        session_name: str,
        entry_id: UUID,
    ) -> None:
        await self._repository.delete_entry(agent_id, session_name, entry_id)
        self._event_pipeline.push(
            TranscriptEntryDeleted(
                agent_id=agent_id,
                session_name=session_name,
                entry_id=entry_id,
            )
        )


__all__ = ["TranscriptService"]
