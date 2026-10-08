"""Failure-path and state-machine coverage for :class:`pairo.core.agent.Agent`."""

import asyncio

import pytest
from pydantic import ConfigDict

from pairo.core.agent import Agent, AgentState
from pairo.core.agent_config import AgentConfiguration, JsonObject, Sandbox
from pairo.core.agent_server import SessionIdentifier, SessionNotification
from pairo.core.events import (
    AgentError,
    ClientResponseCompleted,
    ClientResponseInterrupted,
    EventPipeline,
    SessionUpdated,
)


class LifecycleIdentifier(SessionIdentifier):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = "lifecycle-test"
    revision: int = 0


class ControlledClient:
    """Small deterministic client whose notification stream is test-controlled."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.notifications_queue: asyncio.Queue[SessionNotification | None] = asyncio.Queue()
        self.start_entered = asyncio.Event()
        self.release_start = asyncio.Event()
        self.release_start.set()
        self.start_error: Exception | None = None

    async def new_session(self, configuration, agent_name):
        self.calls.append(("new", agent_name))
        return LifecycleIdentifier()

    async def restore_session(self, configuration, identifier_json: JsonObject):
        self.calls.append(("restore", identifier_json))
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

    async def close(self, session):
        self.calls.append(("close", session.revision))

    async def complete(self, revision: int, message: str = "done") -> None:
        await self.notifications_queue.put(
            SessionNotification(
                session=LifecycleIdentifier(revision=revision),
                event=ClientResponseCompleted(message=message),
            )
        )


def make_agent(client: ControlledClient | None = None):
    pipeline = EventPipeline()
    configured_client = client or ControlledClient()
    agent = Agent(
        name=" developer ",
        config=AgentConfiguration(
            sandbox=Sandbox.READ_ONLY,
            workspace=".",
            model="test-model",
        ),
        client=configured_client,
        event_pipeline=pipeline,
    )
    return agent, configured_client, pipeline


def test_agent_validates_names_messages_sources_and_pre_run_operations() -> None:
    async def scenario() -> None:
        agent, _, pipeline = make_agent()
        assert agent.name == "developer"
        assert agent.state is AgentState.IDLE
        assert not agent.is_active()
        assert agent.last_message() is None

        with pytest.raises(ValueError, match="Session name cannot be empty"):
            await agent.run("  ")
        with pytest.raises(RuntimeError, match="active session"):
            await agent.send_message("hello")

        await agent.run("Main")
        with pytest.raises(ValueError, match="Message cannot be empty"):
            await agent.send_message("  ")
        with pytest.raises(ValueError, match="source cannot be empty"):
            await agent.send_message("hello", source="  ")
        with pytest.raises(RuntimeError, match="already running"):
            await agent.run("Other")

        await agent.close()
        assert agent.state is AgentState.STOPPED
        assert not agent.is_active()
        with pytest.raises(RuntimeError, match="stopped"):
            await agent.run("Again")
        await pipeline.close()

    asyncio.run(scenario())


def test_session_switching_requires_idle_and_known_session() -> None:
    async def scenario() -> None:
        client = ControlledClient()
        client.release_start.clear()
        agent, _, pipeline = make_agent(client)
        await agent.run("First")
        await agent.create_session("Second")
        assert agent.session_names == ("First", "Second")
        assert await agent.switch_session("First") == "First"
        with pytest.raises(KeyError, match="not loaded"):
            await agent.switch_session("Missing")

        await agent.send_message("working")
        await client.start_entered.wait()
        with pytest.raises(RuntimeError, match="while the agent is generating"):
            await agent.create_session("Third")
        with pytest.raises(RuntimeError, match="while the agent is generating"):
            await agent.switch_session("Second")

        client.release_start.set()
        await client.complete(2)
        assert await agent.wait_for_message(timeout=1) == "done"
        await agent.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_queued_messages_are_steered_in_order() -> None:
    async def scenario() -> None:
        client = ControlledClient()
        client.release_start.clear()
        agent, _, pipeline = make_agent(client)
        await agent.run("Main")

        await agent.send_message("first")
        await client.start_entered.wait()
        await agent.send_message("second")
        await agent.send_message("third")
        client.release_start.set()
        await client.complete(4)

        assert await agent.wait_for_message(timeout=1) == "done"
        assert [call for call in client.calls if call[0] == "steer"] == [
            ("steer", "second"),
            ("steer", "third"),
        ]
        assert agent.session_identifier_json("Main")["revision"] == 6
        await agent.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_provider_failure_publishes_error_unblocks_waiter_and_recovers() -> None:
    async def scenario() -> None:
        client = ControlledClient()
        client.start_error = RuntimeError("provider unavailable")
        agent, _, pipeline = make_agent(client)
        await agent.run("Main")
        await agent.send_message("first")

        with pytest.raises(RuntimeError, match="provider unavailable"):
            await agent.wait_for_message(timeout=1)
        assert agent.state is AgentState.IDLE
        assert any(
            isinstance(event, AgentError) and event.message == "provider unavailable"
            for event in pipeline.events
        )

        client.start_error = None
        await agent.send_message("retry")
        await client.complete(2, "recovered")
        assert await agent.wait_for_message(timeout=1) == "recovered"
        await agent.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_premature_notification_stream_is_reported_as_an_error() -> None:
    async def scenario() -> None:
        agent, client, pipeline = make_agent()
        await agent.run("Main")
        await agent.send_message("hello")
        await client.notifications_queue.put(None)

        with pytest.raises(RuntimeError, match="ended before completion"):
            await agent.wait_for_message(timeout=1)
        assert agent.state is AgentState.IDLE
        assert isinstance(pipeline.events[-1], AgentError)
        await agent.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_unchanged_identifiers_do_not_publish_duplicate_updates() -> None:
    async def scenario() -> None:
        agent, client, pipeline = make_agent()
        await agent.run("Main")
        await agent.send_message("hello")
        await client.complete(1)
        assert await agent.wait_for_message(timeout=1) == "done"

        updates = [event for event in pipeline.events if isinstance(event, SessionUpdated)]
        assert [event.identifier["revision"] for event in updates] == [0, 1]
        await agent.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_interrupted_turn_unblocks_wait_for_message() -> None:
    async def scenario() -> None:
        agent, client, pipeline = make_agent()
        await agent.run("Main")
        await agent.send_message("hello")
        await client.notifications_queue.put(
            SessionNotification(
                session=LifecycleIdentifier(revision=1),
                event=ClientResponseInterrupted(),
            )
        )
        result = await asyncio.wait_for(agent.wait_for_message(), timeout=0.05)
        await agent.close()
        await pipeline.close()
        assert result is None

    asyncio.run(scenario())
