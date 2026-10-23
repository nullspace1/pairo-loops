"""Tests for rate-limited durable activity progress."""

import asyncio
from collections.abc import Awaitable
from uuid import uuid4

import pytest

from pairo.ui.activity_progress_coordinator import (
    ActivityProgressCoordinator,
    ProgressPersistenceError,
)
from pairo.ui.activity_terminalization import ActivityTerminalOutcome
from pairo.ui.models import EventDetailLevel, TranscriptEntry
from pairo.ui.transcribers.routed_transcript_entry import RoutedTranscriptEntry


class ControlledSleep:
    def __init__(self) -> None:
        self._waiters: list[asyncio.Future[None]] = []

    async def __call__(self, _seconds: float) -> None:
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        await waiter

    def release_next(self) -> None:
        self._waiters.pop(0).set_result(None)


class RecordingTranscriptService:
    def __init__(self, *, fail_content: str | None = None) -> None:
        self.writes: list[str] = []
        self.terminalized: list[tuple[str, str, ActivityTerminalOutcome]] = []
        self._fail_content = fail_content

    async def upsert(self, _agent_id: object, _session_name: str, entry: TranscriptEntry[object]) -> None:
        if entry.content == self._fail_content:
            raise OSError("disk unavailable")
        self.writes.append(entry.content)

    async def terminalize_pending_activities(self, agent_id: object, session_name: str, outcome: ActivityTerminalOutcome) -> None:
        self.terminalized.append((str(agent_id), session_name, outcome))


def routed(agent_id: object, session: str, entry_id: object, content: str) -> RoutedTranscriptEntry[object]:
    return RoutedTranscriptEntry(agent_id, session, TranscriptEntry("agent", content, EventDetailLevel.NORMAL, {"type": "command_execution_progress"}, entry_id), entry_id)


def test_burst_keeps_latest_and_keys_progress_independently() -> None:
    async def scenario() -> None:
        service = RecordingTranscriptService()
        sleep = ControlledSleep()
        coordinator = ActivityProgressCoordinator(service, cadence_seconds=1, sleep=sleep)  # type: ignore[arg-type]
        agent = uuid4()
        first, second = uuid4(), uuid4()
        await coordinator.submit_progress(routed(agent, "one", first, "first"))
        await coordinator.submit_progress(routed(agent, "one", first, "latest"))
        await coordinator.submit_progress(routed(agent, "two", second, "other-first"))
        await asyncio.sleep(0)
        assert service.writes == ["first", "other-first"]
        sleep.release_next(); sleep.release_next()
        await asyncio.sleep(0)
        assert service.writes == ["first", "other-first", "latest"]
        await coordinator.shutdown()
    asyncio.run(scenario())


def test_completion_and_terminalization_cancel_stale_progress() -> None:
    async def scenario() -> None:
        service = RecordingTranscriptService()
        sleep = ControlledSleep()
        coordinator = ActivityProgressCoordinator(service, sleep=sleep)  # type: ignore[arg-type]
        agent, completed, interrupted = uuid4(), uuid4(), uuid4()
        await coordinator.submit_progress(routed(agent, "main", completed, "old"))
        await coordinator.submit_progress(routed(agent, "main", completed, "pending"))
        await coordinator.complete(routed(agent, "main", completed, "complete"))
        await coordinator.submit_progress(routed(agent, "main", interrupted, "old-terminal"))
        await coordinator.submit_progress(routed(agent, "main", interrupted, "latest-terminal"))
        await coordinator.terminalize_session(agent, "main", ActivityTerminalOutcome.ERROR)
        await asyncio.sleep(0)
        assert service.writes == ["old", "complete", "old-terminal", "latest-terminal"]
        assert service.terminalized == [(str(agent), "main", ActivityTerminalOutcome.ERROR)]
        await coordinator.shutdown()
    asyncio.run(scenario())


def test_shutdown_flushes_and_surfaces_deferred_persistence_failure() -> None:
    async def scenario() -> None:
        sleep = ControlledSleep()
        service = RecordingTranscriptService(fail_content="bad")
        coordinator = ActivityProgressCoordinator(service, sleep=sleep)  # type: ignore[arg-type]
        agent, entry_id = uuid4(), uuid4()
        await coordinator.submit_progress(routed(agent, "main", entry_id, "good"))
        await coordinator.submit_progress(routed(agent, "main", entry_id, "bad"))
        await asyncio.sleep(0)
        sleep.release_next()
        await asyncio.sleep(0)
        with pytest.raises(ProgressPersistenceError, match="Deferred"):
            await coordinator.shutdown()
    asyncio.run(scenario())
