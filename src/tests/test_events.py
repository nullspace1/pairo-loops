"""Tests for ordered asyncio-native event delivery."""

import asyncio
from threading import Thread, get_ident
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from pairo.core.events import (
    AgentActivityCompleted,
    AgentActivityProgress,
    AgentActivityStarted,
    AgentPlanDelta,
    AgentReasoningDelta,
    AgentResponseCompleted,
    AgentResponseInterrupted,
    AgentTextDelta,
    AgentTokenUsageUpdated,
    ClientActivityCompleted,
    ClientEvent,
    ClientActivityProgress,
    ClientActivityStarted,
    ClientPlanDelta,
    ClientReasoningDelta,
    ClientResponseCompleted,
    ClientResponseInterrupted,
    ClientTextDelta,
    ClientTokenUsageUpdated,
    EventPipeline,
    MessageSent,
    TokenUsage,
    UserInput,
)


AGENT_ID = uuid4()


class CustomClientEvent(ClientEvent):
    """Test-only event that implements the client routing contract."""

    text: str

    def to_routed_event(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> AgentTextDelta:
        return AgentTextDelta(
            agent_id=agent_id,
            session_name=session_name,
            text=self.text,
        )


class IncompleteClientEvent(ClientEvent):
    pass


def test_custom_client_event_routes_through_its_contract() -> None:
    routed = CustomClientEvent(text="custom").to_routed_event(AGENT_ID, "session")

    assert routed == AgentTextDelta(
        agent_id=AGENT_ID,
        session_name="session",
        text="custom",
    )


def test_client_events_must_implement_the_routing_contract() -> None:
    with pytest.raises(TypeError, match="abstract"):
        IncompleteClientEvent()


def test_every_client_event_has_a_concrete_routed_event() -> None:
    usage = TokenUsage(
        input_tokens=1,
        cached_input_tokens=0,
        output_tokens=2,
        reasoning_output_tokens=0,
        total_tokens=3,
    )
    cases = (
        (ClientTextDelta(text="text"), AgentTextDelta),
        (ClientPlanDelta(text="plan"), AgentPlanDelta),
        (ClientReasoningDelta(text="reason"), AgentReasoningDelta),
        (ClientActivityStarted(activity={"type": "command"}), AgentActivityStarted),
        (ClientActivityProgress(activity={"progress": 1}), AgentActivityProgress),
        (ClientActivityCompleted(activity={"result": "ok"}), AgentActivityCompleted),
        (ClientResponseCompleted(message="done"), AgentResponseCompleted),
        (ClientResponseInterrupted(), AgentResponseInterrupted),
        (
            ClientTokenUsageUpdated(last=usage, total=usage),
            AgentTokenUsageUpdated,
        ),
    )

    for client_event, routed_type in cases:
        routed = client_event.to_routed_event(AGENT_ID, "session")

        assert type(routed) is routed_type
        assert routed.agent_id == AGENT_ID
        assert routed.session_name == "session"
        assert routed.model_dump(exclude={"agent_id", "session_name"}) == (
            client_event.model_dump(exclude={"kind"})
        )


def test_listener_order_consumption_and_one_shot_waiters() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
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
        await pipeline.enqueue(sent)

        assert await waiting is sent
        assert calls == ["first", "second"]
        assert pipeline.events == [sent]

        pipeline.add_listener(UserInput, lambda event: None)
        assert await pipeline.enqueue(UserInput(message="consumed")) is None
        assert len(pipeline.events) == 1
        await pipeline.close()

    asyncio.run(scenario())


def test_listener_can_process_existing_events_on_registration() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        first = MessageSent(
            agent_id=AGENT_ID,
            session_name="s",
            message="first",
        )
        second = MessageSent(
            agent_id=AGENT_ID,
            session_name="s",
            message="second",
        )
        await pipeline.enqueue(first)
        await pipeline.enqueue(second)
        default_calls: list[str] = []
        replayed_calls: list[str] = []

        pipeline.add_listener(
            MessageSent,
            lambda event: default_calls.append(event.message) or event,
        )

        async def replay(event: MessageSent) -> MessageSent:
            await asyncio.sleep(0)
            replayed_calls.append(event.message)
            return event

        replay_task = pipeline.add_listener(
            MessageSent,
            replay,
            process_existing_events=True,
        )
        assert replay_task is not None
        await replay_task

        assert default_calls == []
        assert replayed_calls == ["first", "second"]
        assert pipeline.events == [first, second]
        await pipeline.close()

    asyncio.run(scenario())


def test_enqueue_is_non_blocking_and_receipt_waits_for_processing() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        listener_started = asyncio.Event()
        release_listener = asyncio.Event()

        async def delayed(event: MessageSent) -> MessageSent:
            listener_started.set()
            await release_listener.wait()
            return event

        pipeline.add_listener(MessageSent, delayed)
        event = MessageSent(agent_id=AGENT_ID, session_name="s", message="queued")

        receipt = pipeline.enqueue(event)
        await listener_started.wait()
        waiting = asyncio.create_task(receipt.wait())
        await asyncio.sleep(0)
        assert not waiting.done()

        release_listener.set()
        assert await waiting is event
        await pipeline.close()

    asyncio.run(scenario())


def test_listener_removal_base_class_delivery_and_transformation() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
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

        result = await pipeline.enqueue(
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
        pipeline.bind()
        first = pipeline.listen(MessageSent)
        second = pipeline.listen(MessageSent)
        cancelled = pipeline.listen(MessageSent)
        cancelled.cancel()
        event = MessageSent(agent_id=AGENT_ID, session_name="s", message="ready")

        assert await pipeline.enqueue(event) is event
        assert await first is event
        assert await second is event
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        await pipeline.close()

    asyncio.run(scenario())


def test_listener_errors_propagate_without_recording_the_event() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()

        def fail(_event):
            raise RuntimeError("listener failed")

        pipeline.add_listener(MessageSent, fail)
        with pytest.raises(RuntimeError, match="listener failed"):
            await pipeline.enqueue(
                MessageSent(agent_id=AGENT_ID, session_name="s", message="ready")
            )
        assert pipeline.events == []
        await pipeline.close()

    asyncio.run(scenario())


def test_close_cancels_waiters_and_rejects_future_operations() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        waiting = pipeline.listen(UserInput)
        await pipeline.close()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        with pytest.raises(RuntimeError, match="closed"):
            pipeline.listen(UserInput)
        with pytest.raises(RuntimeError, match="closed"):
            pipeline.add_listener(UserInput, lambda event: event)
        with pytest.raises(RuntimeError, match="closed"):
            pipeline.enqueue(UserInput(message="late"))

    asyncio.run(scenario())


def test_cross_thread_events_and_coroutines_execute_on_the_bound_loop() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        principal_thread = get_ident()
        listener_threads: list[int] = []
        receipts = []
        submitted_results: list[int] = []

        def observe(event: UserInput) -> UserInput:
            listener_threads.append(get_ident())
            return event

        async def submitted() -> int:
            assert get_ident() == principal_thread
            return 42

        pipeline.add_listener(UserInput, observe)

        def sdk_thread() -> None:
            for index in range(3):
                receipts.append(pipeline.enqueue(UserInput(message=str(index))))
            submitted_results.append(pipeline.submit(submitted()).result())

        callback = Thread(target=sdk_thread)
        callback.start()
        while callback.is_alive():
            await asyncio.sleep(0)
        callback.join()
        await asyncio.gather(*(receipt.wait() for receipt in receipts))

        assert [event.message for event in pipeline.events] == ["0", "1", "2"]
        assert listener_threads == [principal_thread] * 3
        assert submitted_results == [42]
        await pipeline.close()

    asyncio.run(scenario())


def test_pipeline_rejects_rebinding_to_another_loop() -> None:
    pipeline = EventPipeline()

    async def bind_once() -> None:
        pipeline.bind()

    asyncio.run(bind_once())
    with pytest.raises(RuntimeError, match="another event loop"):
        asyncio.run(bind_once())


def test_join_waits_for_finite_pipeline_owned_tasks() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()

        async def publish_later() -> None:
            await asyncio.sleep(0)
            await pipeline.enqueue(UserInput(message="from task"))

        pipeline.create_task(publish_later(), name="finite-publisher")
        await pipeline.join()

        assert pipeline.events == [UserInput(message="from task")]
        await pipeline.close()

    asyncio.run(scenario())
