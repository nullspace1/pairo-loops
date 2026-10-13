"""Regression tests for the single-reader Codex notification bridge."""

import asyncio
from enum import Enum
from contextlib import suppress
from pathlib import Path
from threading import Barrier, Event as ThreadEvent, get_ident
from types import SimpleNamespace

from openai_codex.models import Notification, UnknownNotification

from pairo.core.agent import Agent, SandboxMode
from pairo.core.clients.codex_client import (
    CodexAgentClient,
    CodexSessionIdentifier,
)
from pairo.core.events import ClientResponseCompleted, ClientTextDelta


class ActivityStatus(str, Enum):
    RUNNING = "running"


class DumpableActivity:
    def model_dump(self, **_kwargs):
        return {
            "turn": {"status": ActivityStatus.RUNNING},
            "items": {"tool": "search"}.items(),
        }


class FakeCodexSdkClient:
    def __init__(self) -> None:
        self.calls = 0
        self.reader_threads: list[int] = []
        self.turn_ids: list[str] = []
        self._notifications = [
            Notification(method="message/delta", payload=UnknownNotification(params={})),
            Notification(method="message/delta", payload=UnknownNotification(params={})),
            Notification(method="turn/completed", payload=UnknownNotification(params={})),
        ]

    def next_turn_notification(self, turn_id: str) -> Notification:
        self.calls += 1
        self.reader_threads.append(get_ident())
        self.turn_ids.append(turn_id)
        return self._notifications.pop(0)

    def turn_steer(self, thread_id: str, turn_id: str, message: str):
        return SimpleNamespace(turn_id="turn-2")

    def close(self) -> None:
        pass


class BlockingCodexSdkClient:
    def __init__(self) -> None:
        self.read_started = ThreadEvent()
        self.interrupted = ThreadEvent()

    def next_turn_notification(self, turn_id: str) -> Notification:
        self.read_started.set()
        self.interrupted.wait(timeout=2)
        return Notification(
            method="turn/completed",
            payload=UnknownNotification(params={}),
        )

    def turn_interrupt(self, thread_id: str, turn_id: str) -> None:
        self.interrupted.set()

    def close(self) -> None:
        self.interrupted.set()


class ConcurrentCodexSdkClient:
    def __init__(self) -> None:
        self.barrier = Barrier(2)
        self.reader_threads: list[int] = []

    def next_turn_notification(self, turn_id: str) -> Notification:
        self.reader_threads.append(get_ident())
        self.barrier.wait(timeout=2)
        return Notification(
            method="turn/completed",
            payload=UnknownNotification(params={}),
        )

    def close(self) -> None:
        return None


def register(adapter: CodexAgentClient, identifier: CodexSessionIdentifier) -> None:
    adapter._register_session(
        Agent(
            name="test",
            sandbox=SandboxMode.READ_ONLY,
            workspace=Path.cwd(),
            model="test",
        ),
        identifier,
    )


def test_one_reader_bridge_delivers_all_turn_notifications() -> None:
    async def scenario() -> None:
        adapter = CodexAgentClient()
        sdk = FakeCodexSdkClient()
        adapter._client = sdk

        async def translate(identifier, notification):
            if notification.method == "turn/completed":
                return ClientResponseCompleted(message="done")
            return ClientTextDelta(text="delta")

        adapter._translate_notification = translate
        identifier = CodexSessionIdentifier(thread_id="thread-1", turn_id="turn-1")
        register(adapter, identifier)
        notifications = [
            notification
            async for notification in adapter.notifications(identifier)
        ]

        assert sdk.calls == 3
        assert len(sdk.reader_threads) == 3
        assert len(set(sdk.reader_threads)) == 1
        assert sdk.reader_threads[0] != get_ident()
        assert isinstance(notifications[-1].event, ClientResponseCompleted)
        assert notifications[-1].session.turn_id is None
        await adapter.close()

    asyncio.run(scenario())


def test_activity_dump_normalizes_enums_and_mapping_views() -> None:
    assert CodexAgentClient._dump(DumpableActivity()) == {
        "turn": {"status": "running"},
        "items": [["tool", "search"]],
    }


def test_reader_uses_the_identifier_returned_by_steer() -> None:
    async def scenario() -> None:
        adapter = CodexAgentClient()
        sdk = FakeCodexSdkClient()
        adapter._client = sdk

        async def translate(identifier, notification):
            if notification.method == "turn/completed":
                return ClientResponseCompleted(message="done")
            return ClientTextDelta(text="delta")

        adapter._translate_notification = translate
        initial = CodexSessionIdentifier(thread_id="thread-1", turn_id="turn-1")
        register(adapter, initial)
        stream = adapter.notifications(initial)
        first = await anext(stream)
        steered = await adapter.steer(first.session, "change direction")
        remaining = [notification async for notification in stream]

        assert steered.turn_id == "turn-2"
        assert sdk.turn_ids == ["turn-1", "turn-2", "turn-2"]
        assert isinstance(remaining[-1].event, ClientResponseCompleted)
        await adapter.close()

    asyncio.run(scenario())


def test_cancelling_the_stream_interrupts_the_blocking_sdk_read() -> None:
    async def scenario() -> None:
        adapter = CodexAgentClient()
        sdk = BlockingCodexSdkClient()
        adapter._client = sdk

        async def translate(identifier, notification):
            return ClientResponseCompleted(message="done")

        adapter._translate_notification = translate
        identifier = CodexSessionIdentifier(thread_id="thread-1", turn_id="turn-1")
        register(adapter, identifier)
        stream = adapter.notifications(identifier)
        pending = asyncio.create_task(anext(stream))
        assert await asyncio.to_thread(sdk.read_started.wait, 1)

        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending

        assert sdk.interrupted.is_set()
        await adapter.close()

    asyncio.run(scenario())


def test_multiple_agents_can_read_notifications_concurrently() -> None:
    async def scenario() -> None:
        adapter = CodexAgentClient()
        sdk = ConcurrentCodexSdkClient()
        adapter._client = sdk

        async def translate(identifier, notification):
            return ClientResponseCompleted(message=identifier.thread_id)

        adapter._translate_notification = translate
        first = CodexSessionIdentifier(thread_id="thread-1", turn_id="turn-1")
        second = CodexSessionIdentifier(thread_id="thread-2", turn_id="turn-2")
        register(adapter, first)
        register(adapter, second)

        async def collect(identifier):
            return [item async for item in adapter.notifications(identifier)]

        results = await asyncio.gather(collect(first), collect(second))
        assert [items[0].event.message for items in results] == ["thread-1", "thread-2"]
        assert len(set(sdk.reader_threads)) == 2
        await adapter.close()

    asyncio.run(scenario())
