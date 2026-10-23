"""Provider-neutral client and routed agent events."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from ..agent.json_object import JsonObject


# Completion values are wire-level contracts, deliberately independent of an
# SDK's generated enum classes.
CommandExecutionCompletionStatus: TypeAlias = Literal[
    "inProgress", "completed", "failed", "declined"
]
McpToolCallCompletionStatus: TypeAlias = Literal[
    "inProgress", "completed", "failed"
]
ActivityFamily: TypeAlias = Literal["command_execution", "mcp_tool_call"]


class ClientEvent(BaseModel, ABC):
    """A provider-neutral notification that can be routed by an agent."""

    @abstractmethod
    def to_routed_event(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> RoutedAgentEvent:
        """Create this event's agent-routed counterpart."""

    def transcript_message(self) -> str | None:
        """Return a terminal transcript message when this event supplies one."""
        return None


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


class ClientActivityProgress(ClientEvent, ABC):
    """Common subscription type for typed activity progress events."""


class ClientActivityCompleted(ClientEvent, ABC):
    """Common subscription type for typed completed activity events."""


class ClientUnsupportedActivity(ClientEvent):
    """A provider activity deliberately kept out of supported UI families."""

    kind: Literal["unsupported_activity"] = "unsupported_activity"
    description: str = "Unsupported provider activity"

    def to_routed_event(self, agent_id: UUID, session_name: str) -> AgentUnsupportedActivity:
        return AgentUnsupportedActivity(agent_id=agent_id, session_name=session_name, description=self.description)


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

    def transcript_message(self) -> str:
        return self.message


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
    """Common routing fields for events published by an agent."""

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


class AgentActivityProgress(RoutedAgentEvent, ABC):
    """Common subscription type for typed routed activity progress events."""

    @property
    @abstractmethod
    def activity_family(self) -> ActivityFamily:
        """Stable provider-neutral identity used to correlate this activity."""


class AgentActivityCompleted(RoutedAgentEvent, ABC):
    """Common subscription type for typed routed completed activity events."""

    @property
    @abstractmethod
    def activity_family(self) -> ActivityFamily:
        """Stable provider-neutral identity used to correlate this activity."""


class AgentUnsupportedActivity(RoutedAgentEvent):
    """Routed unsupported provider activity; intentionally has no transcript renderer."""

    description: str


class ClientCommandExecutionProgress(ClientActivityProgress):
    kind: Literal["command_execution_progress"] = "command_execution_progress"
    activity_id: str
    output_delta: str

    def to_routed_event(self, agent_id: UUID, session_name: str) -> AgentCommandExecutionProgress:
        return AgentCommandExecutionProgress(agent_id=agent_id, session_name=session_name, activity_id=self.activity_id, output_delta=self.output_delta)


class ClientMcpToolCallProgress(ClientActivityProgress):
    kind: Literal["mcp_tool_call_progress"] = "mcp_tool_call_progress"
    activity_id: str
    message: str

    def to_routed_event(self, agent_id: UUID, session_name: str) -> AgentMcpToolCallProgress:
        return AgentMcpToolCallProgress(agent_id=agent_id, session_name=session_name, activity_id=self.activity_id, message=self.message)


class ClientCommandExecutionCompleted(ClientActivityCompleted):
    kind: Literal["command_execution_completed"] = "command_execution_completed"
    activity_id: str
    command: str
    cwd: str | None = None
    status: CommandExecutionCompletionStatus
    exit_code: int | None = None
    output: str | None = None

    def to_routed_event(self, agent_id: UUID, session_name: str) -> AgentCommandExecutionCompleted:
        return AgentCommandExecutionCompleted(agent_id=agent_id, session_name=session_name, activity_id=self.activity_id, command=self.command, cwd=self.cwd, status=self.status, exit_code=self.exit_code, output=self.output)


class ClientMcpToolCallCompleted(ClientActivityCompleted):
    kind: Literal["mcp_tool_call_completed"] = "mcp_tool_call_completed"
    activity_id: str
    server: str
    tool: str
    arguments: JsonValue
    status: McpToolCallCompletionStatus
    result: JsonValue | None = None
    error: str | None = None

    def to_routed_event(self, agent_id: UUID, session_name: str) -> AgentMcpToolCallCompleted:
        return AgentMcpToolCallCompleted(agent_id=agent_id, session_name=session_name, activity_id=self.activity_id, server=self.server, tool=self.tool, arguments=self.arguments, status=self.status, result=self.result, error=self.error)


class AgentCommandExecutionProgress(AgentActivityProgress):
    activity_id: str
    output_delta: str

    @property
    def activity_family(self) -> Literal["command_execution"]:
        return "command_execution"


class AgentMcpToolCallProgress(AgentActivityProgress):
    activity_id: str
    message: str

    @property
    def activity_family(self) -> Literal["mcp_tool_call"]:
        return "mcp_tool_call"


class AgentCommandExecutionCompleted(AgentActivityCompleted):
    activity_id: str
    command: str
    cwd: str | None = None
    status: CommandExecutionCompletionStatus
    exit_code: int | None = None
    output: str | None = None

    @property
    def activity_family(self) -> Literal["command_execution"]:
        return "command_execution"


class AgentMcpToolCallCompleted(AgentActivityCompleted):
    activity_id: str
    server: str
    tool: str
    arguments: JsonValue
    status: McpToolCallCompletionStatus
    result: JsonValue | None = None
    error: str | None = None

    @property
    def activity_family(self) -> Literal["mcp_tool_call"]:
        return "mcp_tool_call"


class AgentResponseCompleted(RoutedAgentEvent):
    message: str


class AgentResponseInterrupted(RoutedAgentEvent):
    pass


class AgentTokenUsageUpdated(RoutedAgentEvent):
    last: TokenUsage
    total: TokenUsage
    model_context_window: int | None = None


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
    "CommandExecutionCompletionStatus", "McpToolCallCompletionStatus",
    "AgentCommandExecutionCompleted", "AgentCommandExecutionProgress",
    "AgentMcpToolCallCompleted", "AgentMcpToolCallProgress",
    "AgentError", "AgentPlanDelta", "AgentReasoningDelta", "AgentResponseCompleted",
    "AgentResponseInterrupted", "AgentTextDelta", "AgentTokenUsageUpdated",
    "ClientActivityCompleted", "ClientActivityProgress",
    "ClientCommandExecutionCompleted", "ClientCommandExecutionProgress",
    "ClientMcpToolCallCompleted", "ClientMcpToolCallProgress",
    "ClientUnsupportedActivity",
    "ClientActivityStarted", "ClientEvent", "ClientPlanDelta",
    "ActivityFamily",
    "ClientReasoningDelta", "ClientResponseCompleted",
    "ClientResponseInterrupted", "ClientTextDelta", "ClientTokenUsageUpdated",
    "RoutedAgentEvent",
    "AgentUnsupportedActivity",
    "SessionCompacted", "SessionCreated", "SessionDeleted", "SessionUpdated",
    "TokenUsage",
]

