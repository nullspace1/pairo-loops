"""Async storage boundary for session metadata and transcripts."""

from typing import Protocol
from uuid import UUID

from pairo.core.agent import JsonObject

from .models import SessionMetadata, TranscriptEntry


class SessionNotFoundError(LookupError):
    pass


class SessionRepository(Protocol):
    async def save_session(
        self, agent_id: UUID, session_name: str, identifier: JsonObject
    ) -> SessionMetadata: ...

    async def append_entry(
        self, agent_id: UUID, session_name: str, entry: TranscriptEntry
    ) -> None: ...

    async def entries_for(
        self, agent_id: UUID, session_name: str
    ) -> tuple[TranscriptEntry, ...]: ...

    async def get_session(
        self, agent_id: UUID, session_name: str
    ) -> SessionMetadata: ...

    async def sessions_for(self, agent_id: UUID) -> tuple[SessionMetadata, ...]: ...

    async def session_for_identifier(
        self, identifier: JsonObject
    ) -> SessionMetadata: ...

    async def close(self) -> None: ...
