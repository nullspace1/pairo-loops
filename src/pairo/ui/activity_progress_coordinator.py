"""Rate-limit durable live-activity progress snapshots."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from .activity_terminalization import ActivityTerminalOutcome
from .models import TranscriptMetadata
from .transcript_service import TranscriptService
from .transcribers.routed_transcript_entry import RoutedTranscriptEntry

DEFAULT_PROGRESS_CADENCE_SECONDS = 0.1


class ProgressPersistenceError(RuntimeError):
    """A deferred progress write failed and must be observed by the owner."""


@dataclass(frozen=True, slots=True)
class _ActivityKey:
    agent_id: UUID
    session_name: str
    entry_id: UUID


class ActivityProgressCoordinator:
    """Persist first progress promptly and coalesce later snapshots per activity."""

    def __init__(
        self,
        transcript_service: TranscriptService,
        *,
        cadence_seconds: float = DEFAULT_PROGRESS_CADENCE_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._transcript_service = transcript_service
        self._cadence_seconds = cadence_seconds
        self._sleep = sleep
        self._locks: dict[_ActivityKey, asyncio.Lock] = {}
        self._pending: dict[_ActivityKey, RoutedTranscriptEntry[TranscriptMetadata]] = {}
        self._tasks: dict[_ActivityKey, asyncio.Task[None]] = {}
        self._closed: set[_ActivityKey] = set()
        self._terminal_sessions: set[tuple[UUID, str]] = set()
        self._background_failure: Exception | None = None
        self._shutdown = False

    async def submit_progress(self, routed: RoutedTranscriptEntry[TranscriptMetadata]) -> None:
        """Write an idle activity immediately; retain only its newest burst snapshot."""
        self._raise_background_failure()
        key = self._key(routed)
        if self._shutdown or self._session_is_terminal(key) or key in self._closed:
            return
        lock = self._lock_for(key)
        async with lock:
            if self._shutdown or self._session_is_terminal(key) or key in self._closed:
                return
            if key in self._tasks:
                self._pending[key] = routed
                return
            await self._persist(routed)
            self._tasks[key] = asyncio.create_task(self._flush_after_cadence(key))

    async def complete(self, routed: RoutedTranscriptEntry[TranscriptMetadata]) -> None:
        """Replace a live card with its completed representation without stale flushes."""
        self._raise_background_failure()
        key = self._key(routed)
        if self._shutdown or self._session_is_terminal(key):
            return
        task, lock = await self._cancel_and_lock(key)
        try:
            if not self._shutdown and not self._session_is_terminal(key):
                await self._persist(routed)
                self._closed.add(key)
        finally:
            self._pending.pop(key, None)
            lock.release()
            await self._await_cancelled(task)

    async def terminalize_session(
        self,
        agent_id: UUID,
        session_name: str,
        outcome: ActivityTerminalOutcome,
    ) -> None:
        """Flush latest details, then atomically make the session's cards terminal."""
        self._raise_background_failure()
        session = (agent_id, session_name)
        self._terminal_sessions.add(session)
        try:
            matching = tuple(key for key in self._locks if (key.agent_id, key.session_name) == session)
            for key in matching:
                task, lock = await self._cancel_and_lock(key)
                try:
                    pending = self._pending.pop(key, None)
                    if pending is not None:
                        await self._persist(pending)
                    self._closed.add(key)
                finally:
                    lock.release()
                    await self._await_cancelled(task)
            await self._transcript_service.terminalize_pending_activities(agent_id, session_name, outcome)
        finally:
            self._terminal_sessions.remove(session)

    async def shutdown(self) -> None:
        """Flush retained snapshots and join every deferred task before repository close."""
        if self._shutdown:
            self._raise_background_failure()
            return
        self._shutdown = True
        cancelled: list[asyncio.Task[None] | None] = []
        for key in tuple(self._locks):
            task, lock = await self._cancel_and_lock(key)
            cancelled.append(task)
            try:
                pending = self._pending.pop(key, None)
                if pending is not None:
                    await self._persist(pending)
            finally:
                lock.release()
                await self._await_cancelled(task)
        self._raise_background_failure()

    async def _flush_after_cadence(self, key: _ActivityKey) -> None:
        try:
            await self._sleep(self._cadence_seconds)
            lock = self._lock_for(key)
            async with lock:
                if self._tasks.get(key) is not asyncio.current_task():
                    return
                self._tasks.pop(key, None)
                pending = self._pending.pop(key, None)
                if pending is not None and not self._shutdown and not self._session_is_terminal(key) and key not in self._closed:
                    await self._persist(pending)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._background_failure = error
            self._tasks.pop(key, None)

    async def _cancel_and_lock(self, key: _ActivityKey) -> tuple[asyncio.Task[None] | None, asyncio.Lock]:
        lock = self._lock_for(key)
        task = self._tasks.pop(key, None)
        if task is not None:
            task.cancel()
        await lock.acquire()
        return task, lock

    async def _await_cancelled(self, task: asyncio.Task[None] | None) -> None:
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _lock_for(self, key: _ActivityKey) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def _key(self, routed: RoutedTranscriptEntry[TranscriptMetadata]) -> _ActivityKey:
        if routed.correlated_entry_id is None:
            raise ValueError("Live activity progress requires a correlated transcript entry.")
        return _ActivityKey(routed.agent_id, routed.session_name, routed.correlated_entry_id)

    def _session_is_terminal(self, key: _ActivityKey) -> bool:
        return (key.agent_id, key.session_name) in self._terminal_sessions

    async def _persist(self, routed: RoutedTranscriptEntry[TranscriptMetadata]) -> None:
        await self._transcript_service.upsert(routed.agent_id, routed.session_name, routed.entry)

    def _raise_background_failure(self) -> None:
        if self._background_failure is not None:
            raise ProgressPersistenceError("Deferred activity progress persistence failed.") from self._background_failure


__all__ = ["ActivityProgressCoordinator", "DEFAULT_PROGRESS_CADENCE_SECONDS", "ProgressPersistenceError"]
