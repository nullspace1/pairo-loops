"""Async SQLite implementation of the session repository."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import UUID

import aiosqlite

from pairo.core.agent import JsonObject

from .models import (
    EventDetailLevel,
    SessionMetadata,
    TranscriptEntry,
)
from .session_repository import SessionNotFoundError


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SqliteSessionRepository:
    DATABASE_FILENAME = "sessions.sqlite3"

    def __init__(
        self,
        folder: str | Path | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._clock = clock
        self._closed = False
        self._connection: aiosqlite.Connection | None = None
        if folder is None:
            self.database_path: Path | None = None
            self._target = ":memory:"
        else:
            storage_folder = Path(folder)
            storage_folder.mkdir(parents=True, exist_ok=True)
            self.database_path = storage_folder / self.DATABASE_FILENAME
            self._target = str(self.database_path)

    async def save_session(
        self,
        agent_id: UUID,
        session_name: str,
        identifier: JsonObject,
    ) -> SessionMetadata:
        session_name = self._require_value(session_name, "session_name")
        encoded_agent_id = str(agent_id)
        connection = await self._connect()
        timestamp = self._timestamp()
        encoded = json.dumps(identifier, sort_keys=True, separators=(",", ":"))
        await connection.execute(
            """
            INSERT INTO sessions (
                agent_id, session_name, identifier_json,
                created_at, last_updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(agent_id, session_name) DO UPDATE SET
                identifier_json = excluded.identifier_json,
                last_updated_at = excluded.last_updated_at
            """,
            (encoded_agent_id, session_name, encoded, timestamp, timestamp),
        )
        await connection.commit()
        return await self.get_session(agent_id, session_name)

    async def append_entry(
        self,
        agent_id: UUID,
        session_name: str,
        entry: TranscriptEntry,
    ) -> None:
        connection = await self._connect()
        await self._require_registered(agent_id, session_name)
        timestamp = self._timestamp()
        await connection.execute(
            """
            INSERT INTO transcript_entries (
                agent_id, session_name, role, content, detail_level, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(agent_id),
                session_name,
                entry.role,
                entry.content,
                entry.detail_level.value,
                timestamp,
            ),
        )
        await connection.execute(
            """
            UPDATE sessions
            SET last_message_at = ?, last_updated_at = ?
            WHERE agent_id = ? AND session_name = ?
            """,
            (timestamp, timestamp, str(agent_id), session_name),
        )
        await connection.commit()

    async def entries_for(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> tuple[TranscriptEntry, ...]:
        connection = await self._connect()
        await self._require_registered(agent_id, session_name)
        cursor = await connection.execute(
            """
            SELECT role, content, detail_level
            FROM transcript_entries
            WHERE agent_id = ? AND session_name = ?
            ORDER BY entry_order ASC
            """,
            (str(agent_id), session_name),
        )
        rows = await cursor.fetchall()
        return tuple(
            TranscriptEntry(
                row["role"],
                row["content"],
                EventDetailLevel(row["detail_level"]),
                {"type": "historical"},
            )
            for row in rows
        )

    async def get_session(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> SessionMetadata:
        row = await self._session_row(agent_id, session_name)
        return self._metadata_from(row)

    async def sessions_for(self, agent_id: UUID) -> tuple[SessionMetadata, ...]:
        connection = await self._connect()
        cursor = await connection.execute(
            """
            SELECT agent_id, session_name, identifier_json, created_at,
                   last_message_at, last_updated_at
            FROM sessions
            WHERE agent_id = ?
            ORDER BY session_order ASC
            """,
            (str(agent_id),),
        )
        return tuple(self._metadata_from(row) for row in await cursor.fetchall())

    async def session_for_identifier(
        self,
        identifier: JsonObject,
    ) -> SessionMetadata:
        connection = await self._connect()
        encoded = json.dumps(identifier, sort_keys=True, separators=(",", ":"))
        cursor = await connection.execute(
            """
            SELECT agent_id, session_name, identifier_json, created_at,
                   last_message_at, last_updated_at
            FROM sessions
            WHERE identifier_json = ?
            """,
            (encoded,),
        )
        rows = await cursor.fetchall()
        if not rows:
            raise SessionNotFoundError("No session matches the provider identifier.")
        if len(rows) > 1:
            raise RuntimeError("The provider identifier matches multiple sessions.")
        return self._metadata_from(rows[0])

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._connection is not None:
                await self._connection.close()
                self._connection = None

    async def __aenter__(self) -> "SqliteSessionRepository":
        await self._connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _connect(self) -> aiosqlite.Connection:
        if self._closed:
            raise RuntimeError("Session repository is closed.")
        if self._connection is None:
            self._connection = await aiosqlite.connect(self._target)
            self._connection.row_factory = sqlite3.Row
            await self._connection.execute("PRAGMA foreign_keys = ON")
            await self._create_schema(self._connection)
        return self._connection

    @staticmethod
    async def _create_schema(connection: aiosqlite.Connection) -> None:
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_order INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL CHECK (length(agent_id) > 0),
                session_name TEXT NOT NULL CHECK (length(session_name) > 0),
                identifier_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_message_at TEXT,
                last_updated_at TEXT NOT NULL,
                UNIQUE (agent_id, session_name)
            );

            CREATE TABLE IF NOT EXISTS transcript_entries (
                entry_order INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                session_name TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                detail_level TEXT NOT NULL
                    CHECK (detail_level IN ('normal', 'debug')),
                created_at TEXT NOT NULL,
                FOREIGN KEY (agent_id, session_name)
                    REFERENCES sessions (agent_id, session_name)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS transcript_session_order
                ON transcript_entries (agent_id, session_name, entry_order);
            """
        )
        await connection.commit()

    async def _session_row(self, agent_id: UUID, session_name: str) -> sqlite3.Row:
        connection = await self._connect()
        cursor = await connection.execute(
            """
            SELECT agent_id, session_name, identifier_json, created_at,
                   last_message_at, last_updated_at
            FROM sessions
            WHERE agent_id = ? AND session_name = ?
            """,
            (str(agent_id), session_name),
        )
        row = await cursor.fetchone()
        if row is None:
            raise SessionNotFoundError(
                f"Session '{session_name}' is not registered for agent '{agent_id}'."
            )
        return row

    async def _require_registered(self, agent_id: UUID, session_name: str) -> None:
        await self._session_row(agent_id, session_name)

    def _timestamp(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Session repository clock must return an aware datetime.")
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _require_value(value: str, field: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field} cannot be empty.")
        return normalized

    @staticmethod
    def _metadata_from(row: sqlite3.Row) -> SessionMetadata:
        identifier = json.loads(row["identifier_json"])
        if not isinstance(identifier, dict):
            raise ValueError("Stored session identifier must be a JSON object.")
        last_message = row["last_message_at"]
        return SessionMetadata(
            agent_id=UUID(row["agent_id"]),
            session_name=row["session_name"],
            identifier=identifier,
            created_at=datetime.fromisoformat(row["created_at"]),
            last_message_at=(
                datetime.fromisoformat(last_message)
                if last_message is not None
                else None
            ),
            last_updated_at=datetime.fromisoformat(row["last_updated_at"]),
        )


__all__ = ["SqliteSessionRepository"]
