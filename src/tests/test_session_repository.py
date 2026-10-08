"""Async persistence tests for opaque session identifiers."""

import asyncio

from pairo.ui.models import (
    EventDetailLevel,
    TranscriptEntry,
)
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


def test_repository_persists_identifier_and_transcript(tmp_path) -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository(tmp_path)
        await repository.save_session(
            "researcher",
            "Conversation 1",
            {"provider": "fake", "thread": "secret"},
        )
        await repository.append_entry(
            "researcher",
            "Conversation 1",
            TranscriptEntry("user", "hello", EventDetailLevel.NORMAL, {"type": "message"}),
        )
        await repository.close()

        reopened = SqliteSessionRepository(tmp_path)
        metadata = await reopened.get_session("researcher", "Conversation 1")
        assert metadata.identifier == {"provider": "fake", "thread": "secret"}
        assert await reopened.entries_for("researcher", "Conversation 1") == (
            TranscriptEntry("user", "hello", EventDetailLevel.NORMAL, {"type": "historical"}),
        )
        await reopened.close()

    asyncio.run(scenario())
