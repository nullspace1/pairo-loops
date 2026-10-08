"""Metadata, ordering, validation, and lifecycle tests for SQLite storage."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from pairo.ui.models import (
    EventDetailLevel,
    TranscriptEntry,
)
from pairo.ui.session_repository import SessionNotFoundError
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


class AdvancingClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def test_upsert_preserves_creation_time_and_updates_identifier() -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository(clock=AdvancingClock())
        created = await repository.save_session(" agent ", " session ", {"version": 1})
        updated = await repository.save_session("agent", "session", {"version": 2})

        assert created.agent_name == "agent"
        assert created.session_name == "session"
        assert updated.identifier == {"version": 2}
        assert updated.created_at == created.created_at
        assert updated.last_updated_at > created.last_updated_at
        assert updated.last_message_at is None
        await repository.close()

    asyncio.run(scenario())


def test_transcripts_are_ordered_isolated_and_update_metadata() -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository(clock=AdvancingClock())
        await repository.save_session("a", "one", {"id": 1})
        await repository.save_session("a", "two", {"id": 2})
        entries = (
            TranscriptEntry("user", "first", EventDetailLevel.NORMAL, {"type": "message"}),
            TranscriptEntry("agent", "debug", EventDetailLevel.DEBUG, {"type": "message"}),
            TranscriptEntry("agent", "last", EventDetailLevel.NORMAL, {"type": "message"}),
        )
        for entry in entries:
            await repository.append_entry("a", "one", entry)
        await repository.append_entry("a", "two", entries[1])

        persisted_entries = (
            TranscriptEntry("user", "first", EventDetailLevel.NORMAL, {"type": "historical"}),
            TranscriptEntry("agent", "debug", EventDetailLevel.DEBUG, {"type": "historical"}),
            TranscriptEntry("agent", "last", EventDetailLevel.NORMAL, {"type": "historical"}),
        )
        assert await repository.entries_for("a", "one") == persisted_entries
        assert await repository.entries_for("a", "two") == (persisted_entries[1],)
        assert (await repository.get_session("a", "one")).last_message_at is not None
        assert [item.session_name for item in await repository.sessions_for("a")] == [
            "one",
            "two",
        ]
        assert await repository.sessions_for("missing") == ()
        await repository.close()

    asyncio.run(scenario())


def test_lookup_by_identifier_handles_none_one_and_many_matches() -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository()
        identifier = {"provider": "fake", "thread": "one"}
        with pytest.raises(SessionNotFoundError, match="No session matches"):
            await repository.session_for_identifier(identifier)

        await repository.save_session("a", "one", identifier)
        match = await repository.session_for_identifier(
            {"thread": "one", "provider": "fake"}
        )
        assert (match.agent_name, match.session_name) == ("a", "one")

        await repository.save_session("b", "two", identifier)
        with pytest.raises(RuntimeError, match="multiple sessions"):
            await repository.session_for_identifier(identifier)
        await repository.close()

    asyncio.run(scenario())


def test_repository_rejects_invalid_inputs_and_operations_after_close() -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository()
        for agent_name, session_name in (("", "s"), ("a", "  ")):
            with pytest.raises(ValueError, match="cannot be empty"):
                await repository.save_session(agent_name, session_name, {"id": 1})
        with pytest.raises(SessionNotFoundError, match="not registered"):
            await repository.append_entry(
                "a",
                "missing",
                TranscriptEntry("user", "hello", EventDetailLevel.NORMAL, {"type": "message"}),
            )
        with pytest.raises(SessionNotFoundError, match="not registered"):
            await repository.entries_for("a", "missing")

        await repository.close()
        await repository.close()
        with pytest.raises(RuntimeError, match="closed"):
            await repository.sessions_for("a")

    asyncio.run(scenario())


def test_repository_rejects_naive_clock_values() -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository(clock=lambda: datetime(2026, 1, 1))
        with pytest.raises(ValueError, match="aware datetime"):
            await repository.save_session("a", "s", {"id": 1})
        await repository.close()

    asyncio.run(scenario())


def test_repository_async_context_manager_closes_connection() -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository()
        async with repository as opened:
            assert opened is repository
            await opened.save_session("a", "s", {"id": 1})
        with pytest.raises(RuntimeError, match="closed"):
            await repository.get_session("a", "s")

    asyncio.run(scenario())
