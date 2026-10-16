"""Provider-neutral client and routed agent events."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..agent.json_object import JsonObject


class ClientEvent(BaseModel, ABC):
    """A provider-neutral client notification that can be routed by a runner."""

    @abstractmethod
    def to_routed_event(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> RoutedAgentEvent:
        """Create this event's runner-routed counterpart."""


class ClientTextDelta(ClientEvent):
    kind: Literal["text_delta"] = "text_delta"
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


class ClientPlanDelta(ClientEvent):
    kind: Literal["plan_delta"] = "plan_delta"
    text: str

    def to_routed_event(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> AgentPlanDelta:
        return AgentPlanDelta(
            agent_id=agent_id,
            session_name=session_name,
            text=self.text,
        )


class ClientReasoningDelta(ClientEvent):
    kind: Literal["reasoning_delta"] = "reasoning_delta"
    text: str

    def to_routed_event(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> AgentReasoningDelta:
        return AgentReasoningDelta(
            agent_id=agent_id,
            session_name=session_name,
            text=self.text,
        )


class ClientActivityStarted(ClientEvent):
    kind: Literal["activity_started"] = "activity_started"
    activity: JsonObject

    def to_routed_event(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> AgentActivityStarted:
        return AgentActivityStarted(
            agent_id=agent_id,
            session_name=session_name,
            activity=self.activity,
        )


class ClientActivityProgress(ClientEvent):
    kind: Literal["activity_progress"] = "activity_progress"
    activity: JsonObject

    def to_routed_event(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> AgentActivityProgress:
        return AgentActivityProgress(
            agent_id=agent_id,
            session_name=session_name,
            activity=self.activity,
        )


class ClientActivityCompleted(ClientEvent):
    kind: Literal["activity_completed"] = "activity_completed"
    activity: JsonObject

    def to_routed_event(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> AgentActivityCompleted:
        return AgentActivityCompleted(
            agent_id=agent_id,
            session_name=session_name,
            activity=self.activity,
        )


class ClientResponseCompleted(ClientEvent):
    kind: Literal["response_completed"] = "response_completed"
    message: str

    def to_routed_event(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> AgentResponseCompleted:
        return AgentResponseCompleted(
            agent_id=agent_id,
            session_name=session_name,
            message=self.message,
        )


class ClientResponseInterrupted(ClientEvent):
    kind: Literal["response_interrupted"] = "response_interrupted"

    def to_routed_event(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> AgentResponseInterrupted:
        return AgentResponseInterrupted(agent_id=agent_id, session_name=session_name)


class TokenUsage(BaseModel):
    """Provider-neutral token counts for one turn or a complete thread."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    total_tokens: int
    cache_write_input_tokens: int | None = None


class ClientTokenUsageUpdated(ClientEvent):
    kind: Literal["token_usage_updated"] = "token_usage_updated"
    last: TokenUsage
    total: TokenUsage
    model_context_window: int | None = None

    def to_routed_event(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> AgentTokenUsageUpdated:
        return AgentTokenUsageUpdated(
            agent_id=agent_id,
            session_name=session_name,
            last=self.last,
            total=self.total,
            model_context_window=self.model_context_window,
        )


class RoutedAgentEvent(BaseModel):
    """Common routing fields for events published by the runner."""

    model_config = ConfigDict(frozen=True)

    agent_id: UUID
    session_name: str


class AgentTextDelta(RoutedAgentEvent):
    text: str


class AgentPlanDelta(RoutedAgentEvent):
    text: str


class AgentReasoningDelta(RoutedAgentEvent):
    text: str


class AgentActivityStarted(RoutedAgentEvent):
    activity: JsonObject


class AgentActivityProgress(RoutedAgentEvent):
    activity: JsonObject


class AgentActivityCompleted(RoutedAgentEvent):
    activity: JsonObject


class AgentResponseCompleted(RoutedAgentEvent):
    message: str


class AgentResponseInterrupted(RoutedAgentEvent):
    pass


class AgentTokenUsageUpdated(RoutedAgentEvent):
    last: TokenUsage
    total: TokenUsage
    model_context_window: int | None = None


def route_client_event(
    agent_id: UUID,
    session_name: str,
    event: ClientEvent,
) -> RoutedAgentEvent:
    """Attach runner routing to a provider-neutral client notification."""
    return event.to_routed_event(agent_id, session_name)


class SessionUpdated(BaseModel):
    """The latest opaque provider identifier for an ID-routed session."""

    model_config = ConfigDict(frozen=True)

    agent_id: UUID
    session_name: str
    identifier: JsonObject


class SessionCreated(SessionUpdated):
    """A newly created provider session that should be registered by consumers."""


class SessionCompacted(BaseModel):
    """A provider successfully accepted compaction for a session."""

    model_config = ConfigDict(frozen=True)

    agent_id: UUID
    session_name: str


class SessionDeleted(BaseModel):
    """A session was removed and another session selected in its place."""

    model_config = ConfigDict(frozen=True)

    agent_id: UUID
    session_name: str
    selected_session_name: str


class AgentError(BaseModel):
    """A provider operation failed while processing one session turn."""

    model_config = ConfigDict(frozen=True)

    agent_id: UUID
    session_name: str
    message: str


__all__ = [
    "AgentActivityCompleted", "AgentActivityProgress", "AgentActivityStarted",
    "AgentError", "AgentPlanDelta", "AgentReasoningDelta", "AgentResponseCompleted",
    "AgentResponseInterrupted", "AgentTextDelta", "AgentTokenUsageUpdated",
    "ClientActivityCompleted", "ClientActivityProgress",
    "ClientActivityStarted", "ClientEvent", "ClientPlanDelta",
    "ClientReasoningDelta", "ClientResponseCompleted",
    "ClientResponseInterrupted", "ClientTextDelta", "ClientTokenUsageUpdated",
    "RoutedAgentEvent", "route_client_event",
    "SessionCompacted", "SessionCreated", "SessionDeleted", "SessionUpdated",
    "TokenUsage",
]

