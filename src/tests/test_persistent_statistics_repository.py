"""Focused persistence tests for agent- and session-scoped sidebar values."""

import asyncio
import sqlite3
from uuid import uuid4

import pytest

from pairo.ui.session_repository import SessionNotFoundError, StatisticValue
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


def test_agent_statistics_persist_in_insertion_order_and_are_isolated(tmp_path) -> None:
    async def scenario() -> None:
        agent_id, other_agent_id = uuid4(), uuid4()
        repository = SqliteSessionRepository(tmp_path)
        await repository.upsert_agent_statistic(agent_id, StatisticValue("name", "Ada"))
        await repository.upsert_agent_statistic(agent_id, StatisticValue("model", "gpt"))
        await repository.upsert_agent_statistic(agent_id, StatisticValue("name", "Grace"))
        await repository.upsert_agent_statistic(other_agent_id, StatisticValue("name", "Other"))
        await repository.close()

        reopened = SqliteSessionRepository(tmp_path)
        assert await reopened.agent_statistics_for(agent_id) == (
            StatisticValue("name", "Grace"),
            StatisticValue("model", "gpt"),
        )
        assert await reopened.agent_statistics_for(other_agent_id) == (
            StatisticValue("name", "Other"),
        )
        await reopened.remove_agent_statistic(agent_id, "name")
        assert await reopened.agent_statistics_for(agent_id) == (StatisticValue("model", "gpt"),)
        await reopened.close()

    asyncio.run(scenario())


def test_session_statistics_are_isolated_removable_and_cascade_with_session(tmp_path) -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository(tmp_path)
        agent_id, other_agent_id = uuid4(), uuid4()
        await repository.save_session(agent_id, "one", {"id": 1})
        await repository.save_session(agent_id, "two", {"id": 2})
        await repository.save_session(other_agent_id, "one", {"id": 3})
        await repository.upsert_agent_statistic(agent_id, StatisticValue("name", "Ada"))
        await repository.upsert_session_statistic(agent_id, "one", StatisticValue("created", "first"))
        await repository.upsert_session_statistic(agent_id, "one", StatisticValue("status", "short"))
        await repository.upsert_session_statistic(agent_id, "one", StatisticValue("created", "updated"))
        await repository.upsert_session_statistic(agent_id, "two", StatisticValue("created", "second"))
        await repository.upsert_session_statistic(other_agent_id, "one", StatisticValue("created", "other"))
        await repository.close()

        repository = SqliteSessionRepository(tmp_path)

        assert await repository.session_statistics_for(agent_id, "one") == (
            StatisticValue("created", "updated"),
            StatisticValue("status", "short"),
        )
        await repository.remove_session_statistic(agent_id, "one", "status")
        assert await repository.session_statistics_for(agent_id, "one") == (
            StatisticValue("created", "updated"),
        )

        await repository.delete_session(agent_id, "one")
        with pytest.raises(SessionNotFoundError):
            await repository.session_statistics_for(agent_id, "one")
        await repository.save_session(agent_id, "one", {"id": 4})
        assert await repository.session_statistics_for(agent_id, "one") == ()
        assert await repository.session_statistics_for(agent_id, "two") == (
            StatisticValue("created", "second"),
        )
        assert await repository.session_statistics_for(other_agent_id, "one") == (
            StatisticValue("created", "other"),
        )
        assert await repository.agent_statistics_for(agent_id) == (StatisticValue("name", "Ada"),)
        await repository.close()

    asyncio.run(scenario())


def test_statistics_schema_is_additive_for_an_existing_session_database(tmp_path) -> None:
    async def scenario() -> None:
        agent_id, entry_id = uuid4(), uuid4()
        connection = sqlite3.connect(tmp_path / SqliteSessionRepository.DATABASE_FILENAME)
        connection.executescript(
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
                entry_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                session_name TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                detail_level TEXT NOT NULL, metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (agent_id, session_name)
                    REFERENCES sessions (agent_id, session_name) ON DELETE CASCADE,
                UNIQUE (agent_id, session_name, entry_id)
            );
            """
        )
        connection.execute(
            "INSERT INTO sessions VALUES (1, ?, 'legacy', '{\"id\":1}', '2026-01-01T00:00:00+00:00', NULL, '2026-01-01T00:00:00+00:00')",
            (str(agent_id),),
        )
        connection.execute(
            "INSERT INTO transcript_entries VALUES (1, ?, ?, 'legacy', 'user', 'hello', 'normal', '{\"type\":\"message\"}', '2026-01-01T00:00:00+00:00')",
            (str(entry_id), str(agent_id)),
        )
        connection.commit()
        connection.close()

        repository = SqliteSessionRepository(tmp_path)
        assert (await repository.get_session(agent_id, "legacy")).identifier == {"id": 1}
        assert (await repository.entries_for(agent_id, "legacy"))[0].content == "hello"
        await repository.upsert_session_statistic(agent_id, "legacy", StatisticValue("status", "kept"))
        assert await repository.session_statistics_for(agent_id, "legacy") == (
            StatisticValue("status", "kept"),
        )
        await repository.close()

    asyncio.run(scenario())

