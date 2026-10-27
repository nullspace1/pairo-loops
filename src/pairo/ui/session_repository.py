"""Async storage boundary for session metadata, transcripts, and sidebar values."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pairo.core.agent import JsonObject

from .models import SessionMetadata, TranscriptEntry, TranscriptMetadata


class SessionNotFoundError(LookupError):
    pass


class SessionNameConflictError(ValueError):
    """Raised when a rename target is already registered for an agent."""


@dataclass(frozen=True, slots=True)
class StatisticValue:
    """One durable, display-ready sidebar value identified by its statistic key."""

    key: str
    value: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("Statistic key cannot be empty.")


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

    async def latest_session_for(self, agent_id: UUID) -> SessionMetadata | None: ...

    async def delete_session(self, agent_id: UUID, session_name: str) -> None: ...

    async def rename_session(
        self, agent_id: UUID, old_name: str, new_name: str
    ) -> SessionMetadata: ...

    async def session_for_identifier(
        self, identifier: JsonObject
    ) -> SessionMetadata: ...

    async def upsert_agent_statistic(
        self, agent_id: UUID, statistic: StatisticValue
    ) -> None: ...

    async def remove_agent_statistic(self, agent_id: UUID, key: str) -> None: ...

    async def agent_statistics_for(self, agent_id: UUID) -> tuple[StatisticValue, ...]: ...

    async def upsert_session_statistic(
        self, agent_id: UUID, session_name: str, statistic: StatisticValue
    ) -> None: ...

    async def remove_session_statistic(
        self, agent_id: UUID, session_name: str, key: str
    ) -> None: ...

    async def session_statistics_for(
        self, agent_id: UUID, session_name: str
    ) -> tuple[StatisticValue, ...]: ...

    async def close(self) -> None: ...
