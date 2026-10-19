"""Notification ordering and shutdown tests for the asynchronous Codex agent."""

import asyncio
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace

from pairo.core.agent import AgentConfiguration, SandboxMode
from pairo.core.clients import AgentStatus
from pairo.core.clients.codex_client import CodexAgent
from pairo.core.events import (
    AgentResponseCompleted,
    AgentTextDelta,
    ClientResponseCompleted,
    ClientTextDelta,
    EventPipeline,
    MessageSent,
    SessionCreated,
)


class NotificationClient:
    def __init__(self) -> None:
        self.notifications = asyncio.Queue()
        self.turn_ids: list[str] = []
        self.reader_threads: list[int] = []
        self.closed = False
        self.read_started = asyncio.Event()

    async def start(self) -> None:
        pass

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True
        await self.notifications.put(SimpleNamespace(kind="closed"))

    async def thread_start(self, _params):
        return SimpleNamespace(thread=SimpleNamespace(id="thread-1"))

    async def turn_start(self, _thread_id: str, _message: str):
        return SimpleNamespace(turn=SimpleNamespace(id="turn-1"))

    async def turn_steer(self, _thread_id: str, _turn_id: str, _message: str):
        return SimpleNamespace(turn_id="turn-2")

    async def next_turn_notification(self, turn_id: str):
        self.read_started.set()
        self.turn_ids.append(turn_id)
        self.reader_threads.append(get_ident())
        return await self.notifications.get()


def make_agent(tmp_path: Path, pipeline: EventPipeline, client: NotificationClient) -> CodexAgent:
    configured = AgentConfiguration(
        name="codex",
        sandbox=SandboxMode.READ_ONLY,
        workspace=tmp_path,
        model="test",
    )
    return CodexAgent(configured, pipeline, client)


def test_notifications_run_on_pipeline_loop_and_are_backpressured(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        client = NotificationClient()
        agent = make_agent(tmp_path, pipeline, client)
        gate = asyncio.Event()
        observed: list[str] = []

        async def slow_listener(event: AgentTextDelta):
            observed.append(event.text)
            await gate.wait()
            return event

        pipeline.add_listener(AgentTextDelta, slow_listener)

        async def translate(_identifier, notification):
            if notification.kind == "terminal":
                return ClientResponseCompleted(message="done")
            return ClientTextDelta(text=notification.kind)

        agent._translate_notification = translate
        await agent.new_session("Conversation 1")
        principal_thread = get_ident()
        await agent.send_message("hello")
        await client.notifications.put(SimpleNamespace(kind="first"))
        await client.notifications.put(SimpleNamespace(kind="terminal"))

        while observed != ["first"]:
            await asyncio.sleep(0)
        assert client.turn_ids == ["turn-1"]
        gate.set()
        while agent.status() is AgentStatus.GENERATING:
            await asyncio.sleep(0)

        assert client.turn_ids == ["turn-1", "turn-1"]
        assert client.reader_threads == [principal_thread, principal_thread]
        assert [type(event) for event in pipeline.events] == [
            SessionCreated,
            MessageSent,
            AgentTextDelta,
            AgentResponseCompleted,
            MessageSent,
        ]
        await agent.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_steering_updates_internal_turn_without_changing_session_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        client = NotificationClient()
        agent = make_agent(tmp_path, pipeline, client)

        async def translate(_identifier, _notification):
            return ClientResponseCompleted(message="done")

        agent._translate_notification = translate
        active = await agent.new_session("Conversation 1")
        await agent.send_message("start")
        await agent.send_message("steer")
        await client.notifications.put(SimpleNamespace(kind="terminal"))
        while agent.status() is AgentStatus.GENERATING:
            await asyncio.sleep(0)

        assert client.turn_ids == ["turn-2"]
        assert agent.current_session() == active
        assert "turn" not in active.identifier.model_dump(mode="json")
        await agent.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_close_closes_transport_before_waiting_for_blocked_read(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        client = NotificationClient()
        agent = make_agent(tmp_path, pipeline, client)

        async def translate(_identifier, _notification):
            return ClientResponseCompleted(message="closed")

        agent._translate_notification = translate
        await agent.new_session("Conversation 1")
        await agent.send_message("start")
        await client.read_started.wait()
        await asyncio.wait_for(agent.close(), timeout=1)

        assert client.closed
        assert agent.status() is AgentStatus.INACTIVE
        await pipeline.close()

    asyncio.run(scenario())
