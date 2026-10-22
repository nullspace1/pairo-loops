"""Lifecycle and ordering tests for the deterministic fake Agent."""

import asyncio
from pathlib import Path
from threading import active_count

import pytest
from pydantic import BaseModel

from pairo.core.agent import AgentConfiguration, SandboxPermission
from pairo.core.clients import AgentStatus, FakeAgent
from pairo.core.events import (
    AgentError,
    AgentResponseCompleted,
    AgentResponseInterrupted,
    EventPipeline,
    MessageSent,
    SessionCreated,
    SessionCompacted,
    SessionUpdated,
    Subscription,
)


def test_fake_agent_exposes_compacting_and_rejects_messages(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        agent = make_agent(tmp_path, pipeline)
        await agent.new_session("one")
        events: list[BaseModel] = []
        subscription = pipeline.subscribe(
            BaseModel, lambda event, _subscription: events.append(event)
        )

        compacting = asyncio.create_task(agent.compact())
        await asyncio.sleep(0)
        assert agent.status() is AgentStatus.COMPACTING
        with pytest.raises(RuntimeError, match="Compaction is in progress"):
            await agent.send_message("continue")
        await compacting
        await subscription.wait_until_idle()

        assert agent.status() is AgentStatus.IDLE
        assert sum(isinstance(event, SessionCompacted) for event in events) == 1
        assert not any(
            isinstance(event, MessageSent) and event.message == "continue"
            for event in events
        )
        pipeline.unsubscribe(subscription)
        await agent.close()

    asyncio.run(scenario())


def make_agent(
    tmp_path: Path,
    pipeline: EventPipeline,
    response_factory=lambda: "response",
) -> FakeAgent:
    return FakeAgent(
        AgentConfiguration(
            name="fake",
            sandbox=SandboxPermission.READ_ONLY,
            workspace=tmp_path,
            model="fake",
        ),
        pipeline,
        response_factory,
    )


def test_turn_events_are_ordered_and_use_no_agent_threads(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        agent = make_agent(tmp_path, pipeline)
        threads_before = active_count()
        events: list[BaseModel] = []

        def collect(
            event: BaseModel,
            _subscription: Subscription[BaseModel],
        ) -> None:
            events.append(event)

        subscription = pipeline.subscribe(BaseModel, collect)

        await agent.new_session("one")
        await agent.send_message("hello")
        while agent.status() is AgentStatus.GENERATING:
            await asyncio.sleep(0)
        while len(events) < 4:
            await asyncio.sleep(0)

        assert active_count() == threads_before
        assert [type(event) for event in events] == [
            SessionCreated,
            MessageSent,
            AgentResponseCompleted,
            MessageSent,
        ]
        assert isinstance(events[-1], MessageSent)
        assert events[-1].source == "fake"
        pipeline.unsubscribe(subscription)
        await agent.close()

    asyncio.run(scenario())


def test_activation_steering_and_interruption_share_one_async_lifecycle(
    tmp_path: Path,
) -> None:
    responses = iter(("first", "steered", "unused"))

    async def scenario() -> None:
        pipeline = EventPipeline()
        agent = make_agent(tmp_path, pipeline, lambda: next(responses))
        events: list[BaseModel] = []
        subscription = pipeline.subscribe(
            BaseModel,
            lambda event, _subscription: events.append(event),
        )
        active = await agent.activate_session(
            "restored",
            {"provider": "fake", "session_id": "session-1"},
        )
        await agent.send_message("first")
        await agent.send_message("steer")
        while agent.status() is AgentStatus.GENERATING:
            await asyncio.sleep(0)
        while not any(isinstance(event, AgentResponseCompleted) for event in events):
            await asyncio.sleep(0)
        completion = next(
            event for event in events if isinstance(event, AgentResponseCompleted)
        )
        assert completion.message == "steered"
        assert active.identifier.session_id == "session-1"
        assert isinstance(events[0], SessionUpdated)

        await agent.send_message("interrupt me")
        assert await agent.interrupt()
        while agent.status() is AgentStatus.GENERATING:
            await asyncio.sleep(0)
        while not isinstance(events[-1], AgentResponseInterrupted):
            await asyncio.sleep(0)
        assert isinstance(events[-1], AgentResponseInterrupted)
        assert not await agent.interrupt()
        pipeline.unsubscribe(subscription)
        await agent.close()

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
        agent = make_agent(tmp_path, pipeline, response_factory)
        events: list[BaseModel] = []
        subscription = pipeline.subscribe(
            BaseModel,
            lambda event, _subscription: events.append(event),
        )
        await agent.new_session("one")

        with pytest.raises(RuntimeError, match="factory failed"):
            await agent.send_message("fail")
        assert agent.status() is AgentStatus.IDLE
        while not any(isinstance(event, AgentError) for event in events):
            await asyncio.sleep(0)
        error = next(event for event in events if isinstance(event, AgentError))
        assert error.session_name == "one"

        await agent.send_message("retry")
        while agent.status() is AgentStatus.GENERATING:
            await asyncio.sleep(0)
        while not any(
            isinstance(event, AgentResponseCompleted) and event.message == "recovered"
            for event in events
        ):
            await asyncio.sleep(0)
        assert any(
            isinstance(event, AgentResponseCompleted) and event.message == "recovered"
            for event in events
        )
        pipeline.unsubscribe(subscription)
        await agent.close()

    asyncio.run(scenario())
