"""Provider-neutral client and routed agent events."""

from __future__ import annotations

from typing import Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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


ClientEvent: TypeAlias = (
    ClientTextDelta
    | ClientPlanDelta
    | ClientReasoningDelta
    | ClientActivityStarted
    | ClientActivityProgress
    | ClientActivityCompleted
    | ClientResponseCompleted
    | ClientResponseInterrupted
)


class AgentEvent(BaseModel):
    """A provider-neutral client event routed to one agent ID and session."""

    model_config = ConfigDict(frozen=True)

    agent_id: UUID
    session_name: str
    payload: ClientEvent = Field(discriminator="kind")


class SessionUpdated(BaseModel):
    """The latest opaque provider identifier for an ID-routed session."""

    model_config = ConfigDict(frozen=True)

    agent_id: UUID
    session_name: str
    identifier: JsonObject


class AgentError(BaseModel):
    """A provider operation failed while processing one session turn."""

    model_config = ConfigDict(frozen=True)

    agent_id: UUID
    session_name: str
    message: str


__all__ = [
    "AgentError", "AgentEvent", "ClientActivityCompleted", "ClientActivityProgress",
    "ClientActivityStarted", "ClientEvent", "ClientPlanDelta",
    "ClientReasoningDelta", "ClientResponseCompleted",
    "ClientResponseInterrupted", "ClientTextDelta", "SessionUpdated",
]
