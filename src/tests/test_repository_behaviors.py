"""Metadata, ordering, validation, and lifecycle tests for SQLite storage."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

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
        agent_id = uuid4()
        created = await repository.save_session(agent_id, " session ", {"version": 1})
        updated = await repository.save_session(agent_id, "session", {"version": 2})

        assert created.agent_id == agent_id
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
        agent_id = uuid4()
        await repository.save_session(agent_id, "one", {"id": 1})
        await repository.save_session(agent_id, "two", {"id": 2})
        entries = (
            TranscriptEntry("user", "first", EventDetailLevel.NORMAL, {"type": "message"}),
            TranscriptEntry("agent", "debug", EventDetailLevel.DEBUG, {"type": "message"}),
            TranscriptEntry("agent", "last", EventDetailLevel.NORMAL, {"type": "message"}),
        )
        for entry in entries:
            await repository.add_entry(agent_id, "one", entry)
        await repository.add_entry(agent_id, "two", entries[1])

        assert await repository.entries_for(agent_id, "one") == entries
        assert await repository.entries_for(agent_id, "two") == (entries[1],)
        assert (await repository.get_session(agent_id, "one")).last_message_at is not None
        assert [item.session_name for item in await repository.sessions_for(agent_id)] == [
            "one",
            "two",
        ]
        assert await repository.sessions_for(uuid4()) == ()
        await repository.close()

    asyncio.run(scenario())


def test_lookup_by_identifier_handles_none_one_and_many_matches() -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository()
        identifier = {"provider": "fake", "thread": "one"}
        with pytest.raises(SessionNotFoundError, match="No session matches"):
            await repository.session_for_identifier(identifier)

        first_agent_id = uuid4()
        await repository.save_session(first_agent_id, "one", identifier)
        match = await repository.session_for_identifier(
            {"thread": "one", "provider": "fake"}
        )
        assert (match.agent_id, match.session_name) == (first_agent_id, "one")

        await repository.save_session(uuid4(), "two", identifier)
        with pytest.raises(RuntimeError, match="multiple sessions"):
            await repository.session_for_identifier(identifier)
        await repository.close()

    asyncio.run(scenario())


def test_repository_rejects_invalid_inputs_and_operations_after_close() -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository()
        agent_id = uuid4()
        with pytest.raises(ValueError, match="cannot be empty"):
            await repository.save_session(agent_id, "  ", {"id": 1})
        with pytest.raises(SessionNotFoundError, match="not registered"):
            await repository.append_entry(
                agent_id,
                "missing",
                TranscriptEntry("user", "hello", EventDetailLevel.NORMAL, {"type": "message"}),
            )
        with pytest.raises(SessionNotFoundError, match="not registered"):
            await repository.entries_for(agent_id, "missing")

        await repository.close()
        await repository.close()
        with pytest.raises(RuntimeError, match="closed"):
            await repository.sessions_for(agent_id)

    asyncio.run(scenario())


def test_repository_rejects_naive_clock_values() -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository(clock=lambda: datetime(2026, 1, 1))
        with pytest.raises(ValueError, match="aware datetime"):
            await repository.save_session(uuid4(), "s", {"id": 1})
        await repository.close()

    asyncio.run(scenario())


def test_repository_async_context_manager_closes_connection() -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository()
        async with repository as opened:
            assert opened is repository
            agent_id = uuid4()
            await opened.save_session(agent_id, "s", {"id": 1})
        with pytest.raises(RuntimeError, match="closed"):
            await repository.get_session(agent_id, "s")

    asyncio.run(scenario())


def test_latest_session_lookup_selects_update_order_and_breaks_ties() -> None:
    async def scenario() -> None:
        clock = AdvancingClock()
        repository = SqliteSessionRepository(clock=clock)
        agent_id = uuid4()

        assert await repository.latest_session_for(agent_id) is None

        await repository.save_session(agent_id, "first", {"id": 1})
        await repository.save_session(agent_id, "second", {"id": 2})
        await repository.save_session(agent_id, "first", {"id": 3})

        latest = await repository.latest_session_for(agent_id)
        assert latest is not None
        assert (latest.session_name, latest.identifier) == ("first", {"id": 3})
        assert [item.session_name for item in await repository.sessions_for(agent_id)] == [
            "first",
            "second",
        ]
        await repository.close()

        tied_clock = lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
        tied_repository = SqliteSessionRepository(clock=tied_clock)
        await tied_repository.save_session(agent_id, "earlier", {"id": 1})
        await tied_repository.save_session(agent_id, "later", {"id": 2})

        tied_latest = await tied_repository.latest_session_for(agent_id)
        assert tied_latest is not None
        assert tied_latest.session_name == "later"
        await tied_repository.close()

    asyncio.run(scenario())
