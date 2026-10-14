"""Provider-neutral client and routed agent events."""

from __future__ import annotations

from typing import Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..agent.json_object import JsonObject


class ClientTextDelta(BaseModel):
    kind: Literal["text_delta"] = "text_delta"
    text: str


class ClientPlanDelta(BaseModel):
    kind: Literal["plan_delta"] = "plan_delta"
    text: str


class ClientReasoningDelta(BaseModel):
    kind: Literal["reasoning_delta"] = "reasoning_delta"
    text: str


class ClientActivityStarted(BaseModel):
    kind: Literal["activity_started"] = "activity_started"
    activity: JsonObject


class ClientActivityProgress(BaseModel):
    kind: Literal["activity_progress"] = "activity_progress"
    activity: JsonObject


class ClientActivityCompleted(BaseModel):
    kind: Literal["activity_completed"] = "activity_completed"
    activity: JsonObject


class ClientResponseCompleted(BaseModel):
    kind: Literal["response_completed"] = "response_completed"
    message: str


class ClientResponseInterrupted(BaseModel):
    kind: Literal["response_interrupted"] = "response_interrupted"


class TokenUsage(BaseModel):
    """Provider-neutral token counts for one turn or a complete thread."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    total_tokens: int
    cache_write_input_tokens: int | None = None


class ClientTokenUsageUpdated(BaseModel):
    kind: Literal["token_usage_updated"] = "token_usage_updated"
    last: TokenUsage
    total: TokenUsage
    model_context_window: int | None = None


ClientEvent: TypeAlias = (
    ClientTextDelta
    | ClientPlanDelta
    | ClientReasoningDelta
    | ClientActivityStarted
    | ClientActivityProgress
    | ClientActivityCompleted
    | ClientResponseCompleted
    | ClientResponseInterrupted
    | ClientTokenUsageUpdated
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
    route = {"agent_id": agent_id, "session_name": session_name}
    if isinstance(event, ClientTextDelta):
        return AgentTextDelta(**route, text=event.text)
    if isinstance(event, ClientPlanDelta):
        return AgentPlanDelta(**route, text=event.text)
    if isinstance(event, ClientReasoningDelta):
        return AgentReasoningDelta(**route, text=event.text)
    if isinstance(event, ClientActivityStarted):
        return AgentActivityStarted(**route, activity=event.activity)
    if isinstance(event, ClientActivityProgress):
        return AgentActivityProgress(**route, activity=event.activity)
    if isinstance(event, ClientActivityCompleted):
        return AgentActivityCompleted(**route, activity=event.activity)
    if isinstance(event, ClientResponseCompleted):
        return AgentResponseCompleted(**route, message=event.message)
    if isinstance(event, ClientResponseInterrupted):
        return AgentResponseInterrupted(**route)
    if isinstance(event, ClientTokenUsageUpdated):
        return AgentTokenUsageUpdated(
            **route,
            last=event.last,
            total=event.total,
            model_context_window=event.model_context_window,
        )
    raise TypeError(f"Unsupported client event: {type(event).__name__}")


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
