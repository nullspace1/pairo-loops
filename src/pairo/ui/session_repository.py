"""Async storage boundary for session metadata and transcripts."""

from typing import Protocol
from uuid import UUID

from pairo.core.agent import JsonObject

from .activity_terminalization import PendingActivityTerminalizer
from .models import SessionMetadata, TranscriptEntry, TranscriptMetadata


class SessionNotFoundError(LookupError):
    pass


class SessionRepository(Protocol):
    async def save_session(
        self, agent_id: UUID, session_name: str, identifier: JsonObject
    ) -> SessionMetadata: ...

    async def add_entry(
        self, agent_id: UUID, session_name: str, entry: TranscriptEntry[TranscriptMetadata]
    ) -> None: ...

    async def update_entry(
        self, agent_id: UUID, session_name: str, entry: TranscriptEntry[TranscriptMetadata]
    ) -> None: ...

    async def upsert_entry(
        self, agent_id: UUID, session_name: str, entry: TranscriptEntry[TranscriptMetadata]
    ) -> bool: ...

    async def terminalize_pending_activities(
        self,
        agent_id: UUID,
        session_name: str,
        terminalizer: PendingActivityTerminalizer,
    ) -> tuple[TranscriptEntry[TranscriptMetadata], ...]: ...

    async def delete_entry(
        self, agent_id: UUID, session_name: str, entry_id: UUID
    ) -> None: ...

    async def entries_for(
        self, agent_id: UUID, session_name: str
    ) -> tuple[TranscriptEntry[TranscriptMetadata], ...]: ...

    async def get_session(
        self, agent_id: UUID, session_name: str
    ) -> SessionMetadata: ...

    async def sessions_for(self, agent_id: UUID) -> tuple[SessionMetadata, ...]: ...

    async def delete_session(self, agent_id: UUID, session_name: str) -> None: ...

    async def session_for_identifier(
        self, identifier: JsonObject
    ) -> SessionMetadata: ...

    async def close(self) -> None: ...
