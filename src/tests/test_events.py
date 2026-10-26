"""Tests for principal-loop subscription event delivery."""

import asyncio
from collections.abc import Coroutine
from threading import Thread, get_ident
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from pairo.core.events import (
    AgentActivityCompleted,
    AgentActivityStarted,
    AgentCommandExecutionCompleted,
    AgentPlanDelta,
    AgentReasoningDelta,
    AgentResponseCompleted,
    AgentResponseInterrupted,
    AgentTextDelta,
    AgentTokenUsageUpdated,
    ClientActivityCompleted,
    ClientActivityStarted,
    ClientCommandExecutionCompleted,
    ClientEvent,
    ClientPlanDelta,
    ClientReasoningDelta,
    ClientResponseCompleted,
    ClientResponseInterrupted,
    ClientTextDelta,
    ClientTokenUsageUpdated,
    EventPipeline,
    MessageSent,
    Subscription,
    SynchronousEventPipeline,
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
        (ClientCommandExecutionCompleted(activity_id="command", command="pytest", status="completed"), AgentCommandExecutionCompleted),
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


def test_listen_receives_once_and_unsubscribes() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        waiting = asyncio.create_task(pipeline.listen(MessageSent))
        await asyncio.sleep(0)
        first = MessageSent(agent_id=AGENT_ID, session_name="s", message="first")

        pipeline.push(first)
        assert await waiting is first
        assert not pipeline._subscriptions

        pipeline.push(
            MessageSent(agent_id=AGENT_ID, session_name="s", message="ignored")
        )

    asyncio.run(scenario())


def test_cancelled_listen_removes_its_temporary_subscription() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        waiting = asyncio.create_task(pipeline.listen(UserInput))
        await asyncio.sleep(0)
        assert pipeline._subscriptions

        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting

        assert not pipeline._subscriptions

    asyncio.run(scenario())


def test_sync_and_async_callbacks_receive_their_subscription() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        received: list[tuple[str, Subscription[MessageSent]]] = []
        complete = asyncio.Event()

        def synchronous(
            event: MessageSent,
            subscription: Subscription[MessageSent],
        ) -> None:
            received.append((f"sync:{event.message}", subscription))

        async def asynchronous(
            event: MessageSent,
            subscription: Subscription[MessageSent],
        ) -> None:
            await asyncio.sleep(0)
            received.append((f"async:{event.message}", subscription))
            complete.set()

        sync_subscription = pipeline.subscribe(MessageSent, synchronous)
        async_subscription = pipeline.subscribe(MessageSent, asynchronous)
        pipeline.push(
            MessageSent(agent_id=AGENT_ID, session_name="s", message="ready")
        )
        await complete.wait()

        assert ("sync:ready", sync_subscription) in received
        assert ("async:ready", async_subscription) in received
        pipeline.unsubscribe(sync_subscription)
        pipeline.unsubscribe(async_subscription)

    asyncio.run(scenario())


def test_each_subscription_is_fifo_without_callback_overlap() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        all_received = asyncio.Event()
        received: list[str] = []
        active_callbacks = 0

        async def blocked(
            event: MessageSent,
            _subscription: Subscription[MessageSent],
        ) -> None:
            nonlocal active_callbacks
            active_callbacks += 1
            assert active_callbacks == 1
            if event.message == "first":
                first_started.set()
                await release_first.wait()
            received.append(event.message)
            active_callbacks -= 1
            if len(received) == 3:
                all_received.set()

        subscription = pipeline.subscribe(MessageSent, blocked)
        for message in ("first", "second", "third"):
            pipeline.push(
                MessageSent(agent_id=AGENT_ID, session_name="s", message=message)
            )
        await first_started.wait()
        assert received == []
        release_first.set()
        await all_received.wait()

        assert received == ["first", "second", "third"]
        pipeline.unsubscribe(subscription)

    asyncio.run(scenario())


def test_blocked_subscription_does_not_delay_another_subscription() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        blocker_started = asyncio.Event()
        release_blocker = asyncio.Event()
        observer_finished = asyncio.Event()

        async def block(
            _event: MessageSent,
            _subscription: Subscription[MessageSent],
        ) -> None:
            blocker_started.set()
            await release_blocker.wait()

        def observe(
            _event: MessageSent,
            _subscription: Subscription[MessageSent],
        ) -> None:
            observer_finished.set()

        blocked = pipeline.subscribe(MessageSent, block)
        observed = pipeline.subscribe(MessageSent, observe)
        pipeline.push(MessageSent(agent_id=AGENT_ID, session_name="s", message="x"))

        await blocker_started.wait()
        await asyncio.wait_for(observer_finished.wait(), timeout=1)
        release_blocker.set()
        pipeline.unsubscribe(blocked)
        pipeline.unsubscribe(observed)

    asyncio.run(scenario())


def test_unsubscribe_is_idempotent_and_discards_buffered_events() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        callback_finished = asyncio.Event()
        received: list[str] = []

        async def callback(
            event: MessageSent,
            _subscription: Subscription[MessageSent],
        ) -> None:
            first_started.set()
            await release_first.wait()
            received.append(event.message)
            callback_finished.set()

        subscription = pipeline.subscribe(MessageSent, callback)
        pipeline.push(MessageSent(agent_id=AGENT_ID, session_name="s", message="first"))
        pipeline.push(MessageSent(agent_id=AGENT_ID, session_name="s", message="queued"))
        await first_started.wait()

        pipeline.unsubscribe(subscription)
        pipeline.unsubscribe(subscription)
        release_first.set()
        await callback_finished.wait()
        await asyncio.sleep(0)

        assert received == ["first"]

    asyncio.run(scenario())


def test_subscription_cancellation_can_be_awaited() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        subscription = pipeline.subscribe(
            UserInput,
            lambda _event, _subscription: None,
        )
        cancelled = asyncio.create_task(subscription.wait_until_cancelled())
        await asyncio.sleep(0)
        assert not cancelled.done()

        pipeline.unsubscribe(subscription)
        await cancelled

        # The cancellation signal remains set for later waiters and duplicate
        # unsubscription remains harmless.
        pipeline.unsubscribe(subscription)
        await subscription.wait_until_cancelled()

    asyncio.run(scenario())


def test_callback_can_unsubscribe_itself() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        received: list[str] = []
        finished = asyncio.Event()

        def callback(
            event: MessageSent,
            subscription: Subscription[MessageSent],
        ) -> None:
            received.append(event.message)
            pipeline.unsubscribe(subscription)
            finished.set()

        pipeline.subscribe(MessageSent, callback)
        pipeline.push(MessageSent(agent_id=AGENT_ID, session_name="s", message="first"))
        pipeline.push(MessageSent(agent_id=AGENT_ID, session_name="s", message="queued"))
        await finished.wait()
        await asyncio.sleep(0)

        assert received == ["first"]

    asyncio.run(scenario())


def test_callback_error_reaches_loop_handler_and_delivery_continues() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        loop = asyncio.get_running_loop()
        contexts: list[dict[str, object]] = []
        delivered = asyncio.Event()
        calls = 0

        def exception_handler(
            _loop: asyncio.AbstractEventLoop,
            context: dict[str, object],
        ) -> None:
            contexts.append(context)

        def callback(
            _event: MessageSent,
            _subscription: Subscription[MessageSent],
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("listener failed")
            delivered.set()

        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(exception_handler)
        subscription = pipeline.subscribe(MessageSent, callback)
        try:
            for message in ("first", "second"):
                pipeline.push(
                    MessageSent(agent_id=AGENT_ID, session_name="s", message=message)
                )
            await delivered.wait()
        finally:
            pipeline.unsubscribe(subscription)
            loop.set_exception_handler(previous_handler)

        assert calls == 2
        assert len(contexts) == 1
        assert isinstance(contexts[0]["exception"], RuntimeError)

    asyncio.run(scenario())


def test_base_class_subscriptions_receive_subclass_events() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        received: list[BaseModel] = []
        delivered = asyncio.Event()

        def observe(
            event: BaseModel,
            _subscription: Subscription[BaseModel],
        ) -> None:
            received.append(event)
            delivered.set()

        subscription = pipeline.subscribe(BaseModel, observe)
        event = UserInput(message="input")
        pipeline.push(event)
        await delivered.wait()

        assert received == [event]
        pipeline.unsubscribe(subscription)

    asyncio.run(scenario())


def test_push_is_non_blocking_and_pipeline_has_no_worker_or_history() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        started = asyncio.Event()
        release = asyncio.Event()

        async def callback(
            _event: UserInput,
            _subscription: Subscription[UserInput],
        ) -> None:
            started.set()
            await release.wait()

        subscription = pipeline.subscribe(UserInput, callback)
        assert pipeline.push(UserInput(message="queued")) is None
        await started.wait()

        assert not hasattr(pipeline, "events")
        assert not hasattr(pipeline, "_task")
        release.set()
        pipeline.unsubscribe(subscription)

    asyncio.run(scenario())


def test_synchronous_bridge_uses_the_principal_loop() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        bridge = SynchronousEventPipeline(pipeline)
        principal_thread = get_ident()
        callback_threads: list[int] = []
        messages: list[str] = []
        result: list[int] = []
        delivered = asyncio.Event()

        def observe(
            event: UserInput,
            _subscription: Subscription[UserInput],
        ) -> None:
            callback_threads.append(get_ident())
            messages.append(event.message)
            if len(messages) == 3:
                delivered.set()

        async def operation() -> int:
            assert get_ident() == principal_thread
            return 42

        subscription = pipeline.subscribe(UserInput, observe)

        def external_thread() -> None:
            for index in range(3):
                bridge.push(UserInput(message=str(index)))
            result.append(bridge.run(operation()))

        callback = Thread(target=external_thread)
        callback.start()
        await delivered.wait()
        while callback.is_alive():
            await asyncio.sleep(0)
        callback.join()

        assert messages == ["0", "1", "2"]
        assert callback_threads == [principal_thread] * 3
        assert result == [42]
        pipeline.unsubscribe(subscription)

    asyncio.run(scenario())


def test_synchronous_run_rejects_the_principal_loop() -> None:
    async def operation() -> None:
        return None

    async def scenario() -> None:
        bridge = SynchronousEventPipeline(EventPipeline())
        coroutine: Coroutine[Any, Any, None] = operation()
        with pytest.raises(RuntimeError, match="cannot block"):
            bridge.run(coroutine)

    asyncio.run(scenario())
