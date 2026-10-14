"""Failure-path and state-machine coverage for :class:`pairo.core.agent.Runner`."""

import asyncio
from pathlib import Path

import pytest
from pydantic import ConfigDict

from pairo.core.agent import Agent, AgentState, JsonObject, Runner, SandboxMode, SessionIdentifier
from pairo.core.clients.client import SessionNotification
from pairo.core.events import (
    AgentError,
    ClientResponseCompleted,
    ClientResponseInterrupted,
    EventPipeline,
    SessionCompacted,
    SessionUpdated,
)


class LifecycleIdentifier(SessionIdentifier):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = "lifecycle-test"
    revision: int = 0


class ControlledClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.notifications_queue: asyncio.Queue[SessionNotification | None] = asyncio.Queue()
        self.start_entered = asyncio.Event()
        self.release_start = asyncio.Event()
        self.release_start.set()
        self.start_error: Exception | None = None

    async def new_session(self, agent: Agent):
        self.calls.append(("new", agent.name, agent.model))
        return LifecycleIdentifier()

    async def restore_session(self, agent: Agent, identifier_json: JsonObject):
        self.calls.append(("restore", agent.name, identifier_json))
        return LifecycleIdentifier.model_validate(identifier_json)

    async def start(self, session, message):
        self.calls.append(("start", message))
        self.start_entered.set()
        await self.release_start.wait()
        if self.start_error is not None:
            raise self.start_error
        return LifecycleIdentifier(revision=session.revision + 1)

    async def steer(self, session, message):
        self.calls.append(("steer", message))
        return LifecycleIdentifier(revision=session.revision + 1)

    async def interrupt(self, session):
        self.calls.append(("interrupt", session.revision))
        return LifecycleIdentifier(revision=session.revision + 1)

    async def compact(self, session):
        self.calls.append(("compact", session.revision))

    async def notifications(self, session):
        self.calls.append(("notifications", session.revision))
        while True:
            notification = await self.notifications_queue.get()
            if notification is None:
                return
            yield notification
            if isinstance(
                notification.event,
                (ClientResponseCompleted, ClientResponseInterrupted),
            ):
                return

    async def close(self):
        self.calls.append(("close",))

    async def complete(self, revision: int, message: str = "done") -> None:
        await self.notifications_queue.put(
            SessionNotification(
                session=LifecycleIdentifier(revision=revision),
                event=ClientResponseCompleted(message=message),
            )
        )


class CloseUnblocksNotificationsClient(ControlledClient):
    """Model a provider read that cannot unwind until its transport closes."""

    def __init__(self) -> None:
        super().__init__()
        self.notifications_started = asyncio.Event()
        self.transport_closed = asyncio.Event()

    async def notifications(self, session):
        self.calls.append(("notifications", session.revision))
        self.notifications_started.set()
        try:
            await asyncio.Future()
            if False:
                yield
        finally:
            await self.transport_closed.wait()

    async def close(self) -> None:
        self.calls.append(("close",))
        self.transport_closed.set()


def make_runner(tmp_path: Path, client: ControlledClient | None = None):
    pipeline = EventPipeline()
    configured_client = client or ControlledClient()
    agent = Agent(
        name=" developer ",
        sandbox=SandboxMode.READ_ONLY,
        workspace=tmp_path,
        model="test-model",
    )
    runner = Runner([agent], pipeline, configured_client)
    return agent, runner, configured_client, pipeline


def test_runner_closes_transport_before_waiting_for_cancelled_workers(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        client = CloseUnblocksNotificationsClient()
        agent, runner, _, pipeline = make_runner(tmp_path, client)
        await runner.start()
        await runner.send_message(agent.id, "keep reading")
        await asyncio.wait_for(client.notifications_started.wait(), timeout=1)

        await asyncio.wait_for(runner.close(), timeout=1)

        assert client.transport_closed.is_set()
        assert runner.state(agent.id) is AgentState.STOPPED
        await pipeline.close()

    asyncio.run(scenario())


def test_runner_validates_operations_and_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        agent, runner, _, pipeline = make_runner(tmp_path)
        assert runner.state(agent.id) is AgentState.IDLE
        assert runner.last_message(agent.id) is None
        with pytest.raises(ValueError, match="Session name cannot be empty"):
            await runner.start("  ")
        with pytest.raises(RuntimeError, match="active session"):
            await runner.send_message(agent.id, "hello")

        await runner.create_session(agent.id, "Main")
        with pytest.raises(ValueError, match="Message cannot be empty"):
            await runner.send_message(agent.id, "  ")
        with pytest.raises(ValueError, match="source cannot be empty"):
            await runner.send_message(agent.id, "hello", source="  ")

        await runner.close()
        assert runner.state(agent.id) is AgentState.STOPPED
        with pytest.raises(RuntimeError, match="closed"):
            await runner.create_session(agent.id, "Again")
        await pipeline.close()

    asyncio.run(scenario())


def test_session_switching_requires_idle_and_known_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = ControlledClient()
        client.release_start.clear()
        agent, runner, _, pipeline = make_runner(tmp_path, client)
        await runner.start("First")
        await runner.create_session(agent.id, "Second")
        assert runner.session_names(agent.id) == ("First", "Second")
        assert await runner.switch_session(agent.id, "First") == "First"
        with pytest.raises(KeyError, match="not loaded"):
            await runner.switch_session(agent.id, "Missing")

        await runner.send_message(agent.id, "working")
        await client.start_entered.wait()
        with pytest.raises(RuntimeError, match="while the agent is generating"):
            await runner.create_session(agent.id, "Third")
        with pytest.raises(RuntimeError, match="while the agent is generating"):
            await runner.switch_session(agent.id, "Second")

        client.release_start.set()
        await client.complete(2)
        assert await runner.wait_for_message(agent.id, timeout=1) == "done"
        await runner.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_queued_messages_are_steered_in_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = ControlledClient()
        client.release_start.clear()
        agent, runner, _, pipeline = make_runner(tmp_path, client)
        await runner.start("Main")

        await runner.send_message(agent.id, "first")
        await client.start_entered.wait()
        await runner.send_message(agent.id, "second")
        await runner.send_message(agent.id, "third")
        client.release_start.set()
        await client.complete(2)

        assert await runner.wait_for_message(agent.id, timeout=1) == "done"
        assert [call for call in client.calls if call[0] == "steer"] == [
            ("steer", "second"),
            ("steer", "third"),
        ]
        assert runner.session_identifier_json(agent.id, "Main")["revision"] == 4
        await runner.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_interrupt_while_start_is_pending_is_sent_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = ControlledClient()
        client.release_start.clear()
        agent, runner, _, pipeline = make_runner(tmp_path, client)
        await runner.start("Main")
        await runner.send_message(agent.id, "hello")
        await client.start_entered.wait()
        assert await runner.interrupt(agent.id)
        client.release_start.set()
        await client.notifications_queue.put(
            SessionNotification(
                session=LifecycleIdentifier(revision=2),
                event=ClientResponseInterrupted(),
            )
        )
        assert await runner.wait_for_message(agent.id, timeout=1) is None
        assert len([call for call in client.calls if call[0] == "interrupt"]) == 1
        assert not await runner.interrupt(agent.id)
        await runner.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_provider_failure_unblocks_waiter_and_recovers(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = ControlledClient()
        client.start_error = RuntimeError("provider unavailable")
        agent, runner, _, pipeline = make_runner(tmp_path, client)
        await runner.start("Main")
        await runner.send_message(agent.id, "first")

        with pytest.raises(RuntimeError, match="provider unavailable"):
            await runner.wait_for_message(agent.id, timeout=1)
        await asyncio.sleep(0)
        assert runner.state(agent.id) is AgentState.IDLE
        assert any(
            isinstance(event, AgentError) and event.message == "provider unavailable"
            for event in pipeline.events
        )

        client.start_error = None
        await runner.send_message(agent.id, "retry")
        await client.complete(2, "recovered")
        assert await runner.wait_for_message(agent.id, timeout=1) == "recovered"
        await runner.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_premature_stream_is_error_and_unchanged_updates_are_deduplicated(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        agent, runner, client, pipeline = make_runner(tmp_path)
        await runner.start("Main")
        await runner.send_message(agent.id, "hello")
        await client.notifications_queue.put(None)
        with pytest.raises(RuntimeError, match="ended before completion"):
            await runner.wait_for_message(agent.id, timeout=1)
        assert isinstance(pipeline.events[-1], AgentError)

        await runner.send_message(agent.id, "retry")
        await client.complete(2)
        assert await runner.wait_for_message(agent.id, timeout=1) == "done"
        updates = [event for event in pipeline.events if isinstance(event, SessionUpdated)]
        assert [event.identifier["revision"] for event in updates] == [0, 1, 2]
        await runner.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_new_sessions_receive_latest_agent_configuration(tmp_path: Path) -> None:
    async def scenario() -> None:
        agent, runner, client, pipeline = make_runner(tmp_path)
        await runner.start("First")
        agent.set_model("updated-model")
        await runner.create_session(agent.id, "Second")
        assert [call[2] for call in client.calls if call[0] == "new"] == [
            "test-model",
            "updated-model",
        ]
        await runner.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_compaction_is_delegated_and_publishes_an_event(tmp_path: Path) -> None:
    async def scenario() -> None:
        agent, runner, client, pipeline = make_runner(tmp_path)
        await runner.start("Main")

        assert await runner.compact_session(agent.id) == "Main"

        assert ("compact", 0) in client.calls
        assert SessionCompacted(agent_id=agent.id, session_name="Main") in pipeline.events
        await runner.close()
        await pipeline.close()

    asyncio.run(scenario())
