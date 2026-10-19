"""Lifecycle and ordering tests for the deterministic fake Agent."""

import asyncio
from pathlib import Path
from threading import active_count

import pytest

from pairo.core.agent import AgentConfiguration, SandboxMode
from pairo.core.clients import AgentStatus, FakeAgent
from pairo.core.events import (
    AgentError,
    AgentResponseCompleted,
    AgentResponseInterrupted,
    EventPipeline,
    MessageSent,
    SessionCreated,
    SessionUpdated,
)


def make_agent(
    tmp_path: Path,
    pipeline: EventPipeline,
    response_factory=lambda: "response",
) -> FakeAgent:
    return FakeAgent(
        AgentConfiguration(
            name="fake",
            sandbox=SandboxMode.READ_ONLY,
            workspace=tmp_path,
            model="fake",
        ),
        pipeline,
        response_factory,
    )


def test_turn_events_are_ordered_and_use_no_agent_threads(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        agent = make_agent(tmp_path, pipeline)
        threads_before = active_count()

        await agent.new_session("one")
        await agent.send_message("hello")
        while agent.status() is AgentStatus.GENERATING:
            await asyncio.sleep(0)

        assert active_count() == threads_before
        assert [type(event) for event in pipeline.events] == [
            SessionCreated,
            MessageSent,
            AgentResponseCompleted,
            MessageSent,
        ]
        assert pipeline.events[-1].source == "fake"
        await agent.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_activation_steering_and_interruption_share_one_async_lifecycle(
    tmp_path: Path,
) -> None:
    responses = iter(("first", "steered", "unused"))

    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        agent = make_agent(tmp_path, pipeline, lambda: next(responses))
        active = await agent.activate_session(
            "restored",
            {"provider": "fake", "session_id": "session-1"},
        )
        await agent.send_message("first")
        await agent.send_message("steer")
        while agent.status() is AgentStatus.GENERATING:
            await asyncio.sleep(0)
        completion = next(
            event for event in pipeline.events if isinstance(event, AgentResponseCompleted)
        )
        assert completion.message == "steered"
        assert active.identifier.session_id == "session-1"
        assert isinstance(pipeline.events[0], SessionUpdated)

        await agent.send_message("interrupt me")
        assert await agent.interrupt()
        while agent.status() is AgentStatus.GENERATING:
            await asyncio.sleep(0)
        assert isinstance(pipeline.events[-1], AgentResponseInterrupted)
        assert not await agent.interrupt()
        await agent.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_response_factory_errors_are_contextual_and_recoverable(tmp_path: Path) -> None:
    responses = iter((RuntimeError("factory failed"), "recovered"))

    def response_factory() -> str:
        response = next(responses)
        if isinstance(response, RuntimeError):
            raise response
        return response

    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        agent = make_agent(tmp_path, pipeline, response_factory)
        await agent.new_session("one")

        with pytest.raises(RuntimeError, match="factory failed"):
            await agent.send_message("fail")
        assert agent.status() is AgentStatus.IDLE
        error = next(event for event in pipeline.events if isinstance(event, AgentError))
        assert error.session_name == "one"

        await agent.send_message("retry")
        while agent.status() is AgentStatus.GENERATING:
            await asyncio.sleep(0)
        assert any(
            isinstance(event, AgentResponseCompleted) and event.message == "recovered"
            for event in pipeline.events
        )
        await agent.close()
        await pipeline.close()

    asyncio.run(scenario())
