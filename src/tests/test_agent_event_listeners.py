"""Focused coverage for agent-scoped, non-transforming event observations."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel

from pairo.core.agent import AgentConfiguration, SandboxPermission
from pairo.core.events import (
    AgentActivityCompleted, AgentActivityStarted, AgentError,
    AgentCommandExecutionCompleted,
    AgentMcpToolCallCompleted,
    AgentPlanDelta, AgentReasoningDelta, AgentResponseCompleted,
    AgentResponseInterrupted, AgentTextDelta, AgentTokenUsageUpdated,
    AgentEventListeners, EventPipeline, ImageSent, MessageSent, NetworkMessageSent,
    PermissionRequest, PermissionResponse, SessionCompacted, SessionCreated,
    SessionDeleted, SessionUpdated, TokenUsage, UserInput,
)
from pairo.core.permissions import FileChangeApprovalRequest, PermissionDecision


class ObservedAgent:
    def __init__(self, workspace: Path, name: str = "same display name") -> None:
        self._configuration = AgentConfiguration(
            id=uuid4(),
            name=name,
            sandbox=SandboxPermission.READ_ONLY,
            workspace=workspace,
            model="test-model",
        )

    @property
    def id(self):
        return self._configuration.id

    @property
    def name(self):
        return self._configuration.name

    def configuration(self) -> AgentConfiguration:
        return self._configuration


def _agent(workspace: Path, name: str = "same display name") -> ObservedAgent:
    return ObservedAgent(workspace, name)


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=1, cached_input_tokens=0, output_tokens=2, reasoning_output_tokens=0, total_tokens=3)


def test_every_listener_method_supports_typed_observers_and_optional_agents(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        listeners = AgentEventListeners(pipeline)
        agent = _agent(tmp_path)
        observed: list[BaseModel] = []
        usage = _usage()
        events: tuple[BaseModel, ...] = (
            MessageSent(agent_id=agent.id, session_name="s", message="message"),
            AgentTextDelta(agent_id=agent.id, session_name="s", text="text"),
            AgentPlanDelta(agent_id=agent.id, session_name="s", text="plan"),
            AgentReasoningDelta(agent_id=agent.id, session_name="s", text="reason"),
            AgentActivityStarted(agent_id=agent.id, session_name="s", activity={}),
            AgentCommandExecutionCompleted(agent_id=agent.id, session_name="s", activity_id="command", command="echo", status="completed"),
            AgentMcpToolCallCompleted(agent_id=agent.id, session_name="s", activity_id="tool", server="tools", tool="inspect", arguments={}, status="completed"),
            AgentResponseCompleted(agent_id=agent.id, session_name="s", message="done"),
            AgentResponseInterrupted(agent_id=agent.id, session_name="s"),
            AgentTokenUsageUpdated(agent_id=agent.id, session_name="s", last=usage, total=usage),
            AgentError(agent_id=agent.id, session_name="s", message="failed"),
            SessionCreated(agent_id=agent.id, session_name="s", identifier={}),
            SessionUpdated(agent_id=agent.id, session_name="s", identifier={}),
            SessionCompacted(agent_id=agent.id, session_name="s"),
            SessionDeleted(agent_id=agent.id, session_name="s", selected_session_name="next"),
            PermissionRequest(request_id="r", agent_id=agent.id, session_name="s", request=FileChangeApprovalRequest(session_identifier={})),
            PermissionResponse(request_id="r", agent_id=agent.id, session_name="s", decision=PermissionDecision.ACCEPT),
            NetworkMessageSent(channel_name="team", sender_id=agent.id, sender_name=agent.name, recipient_id=agent.id, session_name="s", message="network"),
        )

        listeners.on_message_sent(observed.append, agent); listeners.on_message_sent(observed.append)
        listeners.on_agent_text_delta(observed.append, agent); listeners.on_agent_text_delta(observed.append)
        listeners.on_agent_plan_delta(observed.append, agent); listeners.on_agent_plan_delta(observed.append)
        listeners.on_agent_reasoning_delta(observed.append, agent); listeners.on_agent_reasoning_delta(observed.append)
        listeners.on_agent_activity_started(observed.append, agent); listeners.on_agent_activity_started(observed.append)
        listeners.on_agent_activity_completed(observed.append, agent); listeners.on_agent_activity_completed(observed.append)
        listeners.on_agent_response_completed(observed.append, agent); listeners.on_agent_response_completed(observed.append)
        listeners.on_agent_response_interrupted(observed.append, agent); listeners.on_agent_response_interrupted(observed.append)
        listeners.on_agent_token_usage_updated(observed.append, agent); listeners.on_agent_token_usage_updated(observed.append)
        listeners.on_agent_error(observed.append, agent); listeners.on_agent_error(observed.append)
        listeners.on_session_created(observed.append, agent); listeners.on_session_created(observed.append)
        listeners.on_session_updated(observed.append, agent); listeners.on_session_updated(observed.append)
        listeners.on_session_compacted(observed.append, agent); listeners.on_session_compacted(observed.append)
        listeners.on_session_deleted(observed.append, agent); listeners.on_session_deleted(observed.append)
        listeners.on_permission_request(observed.append, agent); listeners.on_permission_request(observed.append)
        listeners.on_permission_response(observed.append, agent); listeners.on_permission_response(observed.append)
        listeners.on_network_message_sent(observed.append, agent); listeners.on_network_message_sent(observed.append)

        for event in events:
            pipeline.push(event)
        while len(observed) < 38:
            await asyncio.sleep(0)
        for event in events:
            expected_deliveries = 4 if isinstance(event, SessionCreated) else 2
            assert observed.count(event) == expected_deliveries
        assert not hasattr(listeners, "on_image_sent")
        assert ImageSent is not None

    asyncio.run(scenario())


def test_message_filtering_uses_id_and_registrations_are_future_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        first_agent = _agent(tmp_path)
        second_agent = _agent(tmp_path, "second agent")
        early = MessageSent(agent_id=first_agent.id, session_name="s", message="early")
        pipeline.push(early)
        listeners = AgentEventListeners(pipeline)
        targeted: list[MessageSent] = []
        every_agent: list[MessageSent] = []
        listeners.on_message_sent(targeted.append, first_agent)
        listeners.on_message_sent(every_agent.append)
        first = MessageSent(agent_id=first_agent.id, session_name="s", message="first")
        second = MessageSent(agent_id=second_agent.id, session_name="s", message="second")
        pipeline.push(first)
        pipeline.push(second)
        while len(every_agent) < 2:
            await asyncio.sleep(0)
        assert targeted == [first]
        assert every_agent == [first, second]

    asyncio.run(scenario())



@pytest.mark.parametrize("observer_result", [UserInput(message="ignored"), None])
def test_async_observers_are_awaited_in_order_and_preserve_events(
    tmp_path: Path,
    observer_result: BaseModel | None,
) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        listeners = AgentEventListeners(pipeline)
        agent = _agent(tmp_path)
        calls: list[str] = []
        original = MessageSent(agent_id=agent.id, session_name="s", message="original")

        async def async_observer(event: MessageSent) -> BaseModel | None:
            assert event is original
            calls.append("async started")
            await asyncio.sleep(0)
            calls.append("async completed")
            return observer_result

        def sync_observer(event: MessageSent) -> None:
            assert event is original
            calls.append("sync observed")

        listeners.on_message_sent(async_observer, agent)
        listeners.on_message_sent(sync_observer, agent)

        pipeline.push(original)
        while len(calls) < 3:
            await asyncio.sleep(0)
        assert calls.index("async started") < calls.index("async completed")
        assert "sync observed" in calls

    asyncio.run(scenario())


def test_observers_preserve_events_and_duplicate_detach_is_independent(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        listeners = AgentEventListeners(pipeline)
        agent = _agent(tmp_path)
        calls: list[MessageSent] = []

        def observer(event: MessageSent) -> UserInput:
            calls.append(event)
            return UserInput(message="ignored")

        first_registration = listeners.on_message_sent(observer, agent)
        second_registration = listeners.on_message_sent(observer, agent)
        event = MessageSent(agent_id=agent.id, session_name="s", message="original")
        pipeline.push(event)
        while len(calls) < 2:
            await asyncio.sleep(0)
        assert calls == [event, event]

        pipeline.unsubscribe(first_registration)
        pipeline.unsubscribe(first_registration)
        pipeline.push(event)
        while len(calls) < 3:
            await asyncio.sleep(0)
        assert calls == [event, event, event]

        pipeline.unsubscribe(second_registration)

    asyncio.run(scenario())


def test_observer_failures_reach_the_loop_exception_handler(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        agent = _agent(tmp_path)
        listeners = AgentEventListeners(pipeline)
        loop = asyncio.get_running_loop()
        reported = asyncio.Event()

        def fail(event: MessageSent) -> None:
            raise RuntimeError("observer failed")

        def report(_loop, context) -> None:
            assert str(context["exception"]) == "observer failed"
            reported.set()

        previous = loop.get_exception_handler()
        loop.set_exception_handler(report)
        subscription = listeners.on_message_sent(fail, agent)
        try:
            pipeline.push(
                MessageSent(agent_id=agent.id, session_name="s", message="message")
            )
            await reported.wait()
        finally:
            pipeline.unsubscribe(subscription)
            loop.set_exception_handler(previous)

    asyncio.run(scenario())


def test_network_messages_match_recipient_identity(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        listeners = AgentEventListeners(pipeline)
        recipient = _agent(tmp_path)
        other = _agent(tmp_path)
        delivered: list[NetworkMessageSent] = []
        listeners.on_network_message_sent(delivered.append, recipient)
        event = NetworkMessageSent(channel_name="team", sender_id=other.id, sender_name=other.name, recipient_id=recipient.id, session_name="s", message="hello")
        pipeline.push(event)
        while not delivered:
            await asyncio.sleep(0)
        assert delivered == [event]

    asyncio.run(scenario())
