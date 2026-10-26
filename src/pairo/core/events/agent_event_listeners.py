"""Agent-scoped, non-transforming event observation."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Generic, Protocol, TypeVar, cast
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients.client import Agent

from .agent_events import (
    AgentActivityCompleted,
    AgentActivityStarted,
    AgentError,
    AgentPlanDelta,
    AgentReasoningDelta,
    AgentResponseCompleted,
    AgentResponseInterrupted,
    AgentTextDelta,
    AgentTokenUsageUpdated,
    SessionCompacted,
    SessionCreated,
    SessionDeleted,
    SessionUpdated,
)
from .event_pipeline import EventPipeline, Subscription
from .event_types import EventT
from .message_sent import MessageSent
from .network_message_sent import NetworkMessageSent
from .permission_request import PermissionRequest
from .permission_response import PermissionResponse


ObserverResult = object | Awaitable[object]
EventObserver = Callable[[EventT], ObserverResult]


class _AgentIdentifiedEvent(Protocol):
    agent_id: UUID


class _NetworkRecipientEvent(Protocol):
    recipient_id: UUID


class _EventMatcher(Generic[EventT]):
    def matches(self, event: EventT) -> bool:
        raise NotImplementedError


class _AllEventsMatcher(_EventMatcher[EventT]):
    def matches(self, event: EventT) -> bool:
        return True


class _AgentIdMatcher(_EventMatcher[EventT]):
    """Match events that expose the standard agent_id routing field."""

    def __init__(self, agent_id: UUID) -> None:
        self._agent_id = agent_id

    def matches(self, event: EventT) -> bool:
        return cast(_AgentIdentifiedEvent, event).agent_id == self._agent_id


class _RecipientIdMatcher(_EventMatcher[EventT]):
    """Match network events delivered to the selected agent."""

    def __init__(self, recipient_id: UUID) -> None:
        self._recipient_id = recipient_id

    def matches(self, event: EventT) -> bool:
        return cast(_NetworkRecipientEvent, event).recipient_id == self._recipient_id


class _ObserverAdapter(Generic[EventT]):
    """Keep observer callbacks from changing pipeline processing."""

    def __init__(
        self,
        matcher: _EventMatcher[EventT],
        observer: EventObserver[EventT],
    ) -> None:
        self._matcher = matcher
        self._observer = observer

    async def __call__(
        self,
        event: EventT,
        _subscription: Subscription[EventT],
    ) -> None:
        if self._matcher.matches(event):
            result = self._observer(event)
            if inspect.isawaitable(result):
                await result


class _EventObserverRegistration(Generic[EventT]):
    """Register observers for one event type using its routing policy."""

    def __init__(self, pipeline: EventPipeline, event_type: type[EventT]) -> None:
        self._pipeline = pipeline
        self._event_type = event_type

    def register(
        self,
        observer: EventObserver[EventT],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[EventT]:
        matcher = self._matcher_for(agent)
        adapter = _ObserverAdapter(matcher, observer)
        return self._pipeline.subscribe(self._event_type, adapter)

    def _matcher_for(self, agent: Agent[SessionIdentifier] | None) -> _EventMatcher[EventT]:
        if agent is None:
            return _AllEventsMatcher[EventT]()
        return self._agent_matcher(agent)

    def _agent_matcher(self, agent: Agent[SessionIdentifier]) -> _EventMatcher[EventT]:
        return _AgentIdMatcher[EventT](agent.configuration().id)


class _NetworkMessageObserverRegistration(_EventObserverRegistration[NetworkMessageSent]):
    def _agent_matcher(self, agent: Agent[SessionIdentifier]) -> _EventMatcher[NetworkMessageSent]:
        return _RecipientIdMatcher[NetworkMessageSent](agent.configuration().id)


class AgentEventListeners:
    """Convenience registrations for observing events for one agent or all agents.

    Registrations only observe future events. Their callback result is ignored, so
    they cannot consume or transform an event in the underlying pipeline.
    """

    def __init__(self, pipeline: EventPipeline) -> None:
        self._message_sent = _EventObserverRegistration(pipeline, MessageSent)
        self._agent_text_delta = _EventObserverRegistration(pipeline, AgentTextDelta)
        self._agent_plan_delta = _EventObserverRegistration(pipeline, AgentPlanDelta)
        self._agent_reasoning_delta = _EventObserverRegistration(pipeline, AgentReasoningDelta)
        self._agent_activity_started = _EventObserverRegistration(pipeline, AgentActivityStarted)
        self._agent_activity_completed = _EventObserverRegistration(
            pipeline, cast(type[AgentActivityCompleted], AgentActivityCompleted)
        )
        self._agent_response_completed = _EventObserverRegistration(pipeline, AgentResponseCompleted)
        self._agent_response_interrupted = _EventObserverRegistration(pipeline, AgentResponseInterrupted)
        self._agent_token_usage_updated = _EventObserverRegistration(pipeline, AgentTokenUsageUpdated)
        self._agent_error = _EventObserverRegistration(pipeline, AgentError)
        self._session_created = _EventObserverRegistration(pipeline, SessionCreated)
        self._session_updated = _EventObserverRegistration(pipeline, SessionUpdated)
        self._session_compacted = _EventObserverRegistration(pipeline, SessionCompacted)
        self._session_deleted = _EventObserverRegistration(pipeline, SessionDeleted)
        self._permission_request = _EventObserverRegistration(pipeline, PermissionRequest)
        self._permission_response = _EventObserverRegistration(pipeline, PermissionResponse)
        self._network_message_sent = _NetworkMessageObserverRegistration(pipeline, NetworkMessageSent)

    def on_message_sent(
        self,
        observer: EventObserver[MessageSent],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[MessageSent]:
        return self._message_sent.register(observer, agent)

    def on_agent_text_delta(
        self,
        observer: EventObserver[AgentTextDelta],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[AgentTextDelta]:
        return self._agent_text_delta.register(observer, agent)

    def on_agent_plan_delta(
        self,
        observer: EventObserver[AgentPlanDelta],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[AgentPlanDelta]:
        return self._agent_plan_delta.register(observer, agent)

    def on_agent_reasoning_delta(
        self,
        observer: EventObserver[AgentReasoningDelta],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[AgentReasoningDelta]:
        return self._agent_reasoning_delta.register(observer, agent)

    def on_agent_activity_started(
        self,
        observer: EventObserver[AgentActivityStarted],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[AgentActivityStarted]:
        return self._agent_activity_started.register(observer, agent)

    def on_agent_activity_completed(
        self,
        observer: EventObserver[AgentActivityCompleted],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[AgentActivityCompleted]:
        return self._agent_activity_completed.register(observer, agent)

    def on_agent_response_completed(
        self,
        observer: EventObserver[AgentResponseCompleted],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[AgentResponseCompleted]:
        return self._agent_response_completed.register(observer, agent)

    def on_agent_response_interrupted(
        self,
        observer: EventObserver[AgentResponseInterrupted],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[AgentResponseInterrupted]:
        return self._agent_response_interrupted.register(observer, agent)

    def on_agent_token_usage_updated(
        self,
        observer: EventObserver[AgentTokenUsageUpdated],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[AgentTokenUsageUpdated]:
        return self._agent_token_usage_updated.register(observer, agent)

    def on_agent_error(
        self,
        observer: EventObserver[AgentError],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[AgentError]:
        return self._agent_error.register(observer, agent)

    def on_session_created(
        self,
        observer: EventObserver[SessionCreated],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[SessionCreated]:
        return self._session_created.register(observer, agent)

    def on_session_updated(
        self,
        observer: EventObserver[SessionUpdated],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[SessionUpdated]:
        return self._session_updated.register(observer, agent)

    def on_session_compacted(
        self,
        observer: EventObserver[SessionCompacted],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[SessionCompacted]:
        return self._session_compacted.register(observer, agent)

    def on_session_deleted(
        self,
        observer: EventObserver[SessionDeleted],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[SessionDeleted]:
        return self._session_deleted.register(observer, agent)

    def on_permission_request(
        self,
        observer: EventObserver[PermissionRequest],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[PermissionRequest]:
        return self._permission_request.register(observer, agent)

    def on_permission_response(
        self,
        observer: EventObserver[PermissionResponse],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[PermissionResponse]:
        return self._permission_response.register(observer, agent)

    def on_network_message_sent(
        self,
        observer: EventObserver[NetworkMessageSent],
        agent: Agent[SessionIdentifier] | None = None,
    ) -> Subscription[NetworkMessageSent]:
        return self._network_message_sent.register(observer, agent)


__all__ = ["AgentEventListeners"]


