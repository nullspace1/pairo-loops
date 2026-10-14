"""Async persistence tests for opaque session identifiers."""

import asyncio
from uuid import uuid4

import pytest

from pairo.ui.models import (
    EventDetailLevel,
    TranscriptEntry,
)
from pairo.ui.sqlite_session_repository import SqliteSessionRepository
from pairo.ui.session_repository import SessionNotFoundError


def test_repository_persists_identifier_and_transcript(tmp_path) -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository(tmp_path)
        agent_id = uuid4()
        await repository.save_session(
            agent_id,
            "Conversation 1",
            {"provider": "fake", "thread": "secret"},
        )
        entry = TranscriptEntry(
            "user", "hello", EventDetailLevel.NORMAL, {"type": "message"}
        )
        await repository.add_entry(
            agent_id,
            "Conversation 1",
            entry,
        )
        await repository.close()

        reopened = SqliteSessionRepository(tmp_path)
        metadata = await reopened.get_session(agent_id, "Conversation 1")
        assert metadata.identifier == {"provider": "fake", "thread": "secret"}
        persisted = await reopened.entries_for(agent_id, "Conversation 1")
        assert persisted == (entry,)
        assert persisted[0].entry_id == entry.entry_id
        assert persisted[0].metadata == {"type": "message"}
        await reopened.close()

    asyncio.run(scenario())


def test_repository_deletes_session_and_cascades_transcript(tmp_path) -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository(tmp_path)
        agent_id = uuid4()
        await repository.save_session(agent_id, "Disposable", {"thread": "one"})
        await repository.append_entry(
            agent_id,
            "Disposable",
            TranscriptEntry("user", "remove me", EventDetailLevel.NORMAL, {"type": "message"}),
        )

        await repository.delete_session(agent_id, "Disposable")

        assert await repository.sessions_for(agent_id) == ()
        with pytest.raises(SessionNotFoundError):
            await repository.entries_for(agent_id, "Disposable")
        await repository.close()

    asyncio.run(scenario())
