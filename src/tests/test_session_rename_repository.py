"""Focused durable-session rename behavior tests."""

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from pairo.ui.models import EventDetailLevel, TranscriptEntry
from pairo.ui.session_repository import SessionNameConflictError, SessionNotFoundError, StatisticValue
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


class AdvancingClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def test_rename_preserves_session_data_order_and_persists_after_reopen(tmp_path) -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository(tmp_path, clock=AdvancingClock())
        agent_id = uuid4()
        await repository.save_session(agent_id, "first", {"provider": "fake", "id": 1})
        original = await repository.save_session(agent_id, "middle", {"provider": "fake", "id": 2})
        await repository.save_session(agent_id, "last", {"provider": "fake", "id": 3})
        entries = (
            TranscriptEntry("user", "one", EventDetailLevel.NORMAL, {"type": "message"}),
            TranscriptEntry("agent", "two", EventDetailLevel.DEBUG, {"type": "message"}),
        )
        for entry in entries:
            await repository.add_entry(agent_id, "middle", entry)
        await repository.upsert_session_statistic(agent_id, "middle", StatisticValue("tokens", "2"))
        await repository.upsert_session_statistic(agent_id, "middle", StatisticValue("status", "kept"))
        before_rename = await repository.get_session(agent_id, "middle")

        renamed = await repository.rename_session(agent_id, " middle ", " renamed ")

        assert renamed.session_name == "renamed"
        assert renamed.identifier == original.identifier
        assert renamed.created_at == before_rename.created_at
        assert renamed.last_message_at == (await repository.get_session(agent_id, "renamed")).last_message_at
        assert renamed.last_updated_at == before_rename.last_updated_at
        assert [item.session_name for item in await repository.sessions_for(agent_id)] == [
            "first", "renamed", "last"
        ]
        assert await repository.entries_for(agent_id, "renamed") == entries
        assert await repository.session_statistics_for(agent_id, "renamed") == (
            StatisticValue("tokens", "2"), StatisticValue("status", "kept")
        )
        await repository.close()

        reopened = SqliteSessionRepository(tmp_path)
        assert await reopened.session_for_identifier({"id": 2, "provider": "fake"}) == renamed
        assert await reopened.entries_for(agent_id, "renamed") == entries
        assert await reopened.session_statistics_for(agent_id, "renamed") == (
            StatisticValue("tokens", "2"), StatisticValue("status", "kept")
        )
        await reopened.close()

    asyncio.run(scenario())


def test_rename_rejects_invalid_missing_and_conflicting_names_without_partial_change() -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository()
        agent_id = uuid4()
        entry = TranscriptEntry("user", "kept", EventDetailLevel.NORMAL, {"type": "message"})
        await repository.save_session(agent_id, "source", {"id": 1})
        await repository.save_session(agent_id, "target", {"id": 2})
        await repository.add_entry(agent_id, "source", entry)
        await repository.upsert_session_statistic(agent_id, "source", StatisticValue("status", "kept"))

        with pytest.raises(ValueError, match="old_name cannot be empty"):
            await repository.rename_session(agent_id, "  ", "other")
        with pytest.raises(ValueError, match="new_name cannot be empty"):
            await repository.rename_session(agent_id, "source", "  ")
        with pytest.raises(SessionNotFoundError, match="missing"):
            await repository.rename_session(agent_id, "missing", "other")
        with pytest.raises(SessionNameConflictError, match="already registered"):
            await repository.rename_session(agent_id, "source", "target")

        assert await repository.entries_for(agent_id, "source") == (entry,)
        assert await repository.session_statistics_for(agent_id, "source") == (
            StatisticValue("status", "kept"),
        )
        assert [item.session_name for item in await repository.sessions_for(agent_id)] == ["source", "target"]
        await repository.close()

    asyncio.run(scenario())


def test_rename_rolls_back_when_sqlite_rejects_the_update() -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository()
        agent_id = uuid4()
        await repository.save_session(agent_id, "source", {"id": 1})
        await repository.add_entry(
            agent_id, "source", TranscriptEntry("user", "kept", EventDetailLevel.NORMAL, {"type": "message"})
        )
        connection = await repository._connect()
        await connection.execute(
            """CREATE TRIGGER reject_session_rename BEFORE UPDATE OF session_name ON sessions
               BEGIN SELECT RAISE(ABORT, 'rename rejected'); END"""
        )
        await connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="rename rejected"):
            await repository.rename_session(agent_id, "source", "renamed")

        assert (await repository.get_session(agent_id, "source")).identifier == {"id": 1}
        assert (await repository.entries_for(agent_id, "source"))[0].content == "kept"
        with pytest.raises(SessionNotFoundError):
            await repository.get_session(agent_id, "renamed")
        await repository.close()

    asyncio.run(scenario())


def test_rename_migrates_a_compatible_preexisting_database(tmp_path) -> None:
    async def scenario() -> None:
        agent_id, entry_id = uuid4(), uuid4()
        database = tmp_path / SqliteSessionRepository.DATABASE_FILENAME
        legacy = sqlite3.connect(database)
        legacy.executescript(
            """
            CREATE TABLE sessions (
                session_order INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL, session_name TEXT NOT NULL,
                identifier_json TEXT NOT NULL, created_at TEXT NOT NULL,
                last_message_at TEXT, last_updated_at TEXT NOT NULL,
                UNIQUE (agent_id, session_name)
            );
            CREATE TABLE transcript_entries (
                entry_order INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL, agent_id TEXT NOT NULL, session_name TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL, detail_level TEXT NOT NULL,
                metadata_json TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY (agent_id, session_name)
                    REFERENCES sessions (agent_id, session_name) ON DELETE CASCADE,
                UNIQUE (agent_id, session_name, entry_id)
            );
            """
        )
        legacy.execute(
            "INSERT INTO sessions VALUES (1, ?, 'legacy', '{\"id\":1}', '2026-01-01T00:00:00+00:00', NULL, '2026-01-01T00:00:00+00:00')",
            (str(agent_id),),
        )
        legacy.execute(
            "INSERT INTO transcript_entries VALUES (1, ?, ?, 'legacy', 'user', 'kept', 'normal', '{\"type\":\"message\"}', '2026-01-01T00:00:00+00:00')",
            (str(entry_id), str(agent_id)),
        )
        legacy.commit()
        legacy.close()

        repository = SqliteSessionRepository(tmp_path)
        await repository.upsert_session_statistic(agent_id, "legacy", StatisticValue("status", "kept"))
        await repository.rename_session(agent_id, "legacy", "renamed")

        assert (await repository.entries_for(agent_id, "renamed"))[0].entry_id == entry_id
        assert await repository.session_statistics_for(agent_id, "renamed") == (
            StatisticValue("status", "kept"),
        )
        connection = await repository._connect()
        assert await (await connection.execute("PRAGMA foreign_key_check")).fetchall() == []
        await repository.close()

    asyncio.run(scenario())
