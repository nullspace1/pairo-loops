"""Tests for ordered asyncio-native event delivery."""

import asyncio
from uuid import uuid4

import pytest
from pydantic import BaseModel

from pairo.core.events import EventPipeline, MessageSent, UserInput


AGENT_ID = uuid4()


def test_listener_order_consumption_and_one_shot_waiters() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        calls: list[str] = []

        async def first(event):
            calls.append("first")
            return event

        def second(event):
            calls.append("second")
            return event

        pipeline.add_listener(MessageSent, first)
        pipeline.add_listener(MessageSent, second)
        waiting = pipeline.listen(MessageSent)
        sent = MessageSent(agent_id=AGENT_ID, session_name="s", message="ready")
        await pipeline.publish(sent)

        assert await waiting is sent
        assert calls == ["first", "second"]
        assert pipeline.events == [sent]

        pipeline.add_listener(UserInput, lambda event: None)
        assert await pipeline.publish(UserInput(message="consumed")) is None
        assert len(pipeline.events) == 1
        await pipeline.close()

    asyncio.run(scenario())


def test_listener_removal_base_class_delivery_and_transformation() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        calls: list[str] = []

        def removed(event):
            calls.append("removed")
            return event

        def transform(_event):
            calls.append("transform")
            return UserInput(message="transformed")

        def observe_base(event):
            calls.append(f"base:{type(event).__name__}")
            return event

        pipeline.add_listener(MessageSent, removed)
        pipeline.remove_listener(MessageSent, removed)
        pipeline.add_listener(MessageSent, transform)
        pipeline.add_listener(BaseModel, observe_base)

        result = await pipeline.publish(
            MessageSent(agent_id=AGENT_ID, session_name="s", message="original")
        )
        assert result == UserInput(message="transformed")
        assert calls == ["transform", "base:UserInput"]
        assert pipeline.events == [UserInput(message="transformed")]
        await pipeline.close()

    asyncio.run(scenario())


def test_multiple_waiters_receive_one_event_and_cancelled_waiters_are_safe() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        first = pipeline.listen(MessageSent)
        second = pipeline.listen(MessageSent)
        cancelled = pipeline.listen(MessageSent)
        cancelled.cancel()
        event = MessageSent(agent_id=AGENT_ID, session_name="s", message="ready")

        assert await pipeline.publish(event) is event
        assert await first is event
        assert await second is event
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        await pipeline.close()

    asyncio.run(scenario())


def test_listener_errors_propagate_without_recording_the_event() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()

        def fail(_event):
            raise RuntimeError("listener failed")

        pipeline.add_listener(MessageSent, fail)
        with pytest.raises(RuntimeError, match="listener failed"):
            await pipeline.publish(
                MessageSent(agent_id=AGENT_ID, session_name="s", message="ready")
            )
        assert pipeline.events == []
        await pipeline.close()

    asyncio.run(scenario())


def test_close_cancels_waiters_and_rejects_future_operations() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        waiting = pipeline.listen(UserInput)
        await pipeline.close()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        with pytest.raises(RuntimeError, match="closed"):
            pipeline.listen(UserInput)
        with pytest.raises(RuntimeError, match="closed"):
            pipeline.add_listener(UserInput, lambda event: event)
        with pytest.raises(RuntimeError, match="closed"):
            await pipeline.publish(UserInput(message="late"))

    asyncio.run(scenario())
