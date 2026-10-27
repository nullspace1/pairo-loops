"""Default shared sidebar statistics and display formatters."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent, AgentStatus
from pairo.ui.models import SessionMetadata
from pairo.core.events import (
    AgentError,
    AgentResponseCompleted,
    AgentResponseInterrupted,
    MessageSent,
    SessionCreated,
    SessionUpdated,
)
from pairo.ui.pairo_event_piper import on_pairo

from .fixed_statistic import FixedStatistic
from .statistic import (
    SidebarStatistic,
    SidebarStatisticCategory,
    UpdateAgentStatistic,
)
from .token_usage_statistic import TokenUsageStatistic


class TimestampFormatter:
    """Render optional timestamps in the sidebar's local-time format."""

    def format(self, value: datetime | None) -> str:
        if value is None:
            return "None"
        local = value.astimezone()
        offset = local.strftime("%z")
        readable_offset = f"{offset[:3]}:{offset[3:]}" if offset else ""
        return f"{local:%Y-%m-%d %H:%M:%S} {readable_offset}".rstrip()


class AgentStateStatistic(FixedStatistic):
    """Render agent state immediately from lifecycle events without persistence."""

    def __init__(self, values: "DefaultSidebarStatisticValues") -> None:
        super().__init__("state", "State", values.agent_state)
        self._states: dict[UUID, str] = {}

    async def value(self, agent_id: UUID, session_name: str | None) -> str | None:
        value = await super().value(agent_id, session_name)
        return self._states.get(agent_id, value)

    async def update_agent(self, agent_id: UUID, value: str | None) -> None:
        self.configured_agent(agent_id)
        if value is None:
            self._states.pop(agent_id, None)
        else:
            self._states[agent_id] = value
        self.app.post_message(UpdateAgentStatistic(self.key, value, agent_id))

    @on_pairo(MessageSent)
    @on_pairo(AgentResponseCompleted)
    @on_pairo(AgentResponseInterrupted)
    @on_pairo(AgentError)
    @on_pairo(SessionCreated)
    @on_pairo(SessionUpdated)
    async def receive_idle_event(
        self,
        event: MessageSent
        | AgentResponseCompleted
        | AgentResponseInterrupted
        | AgentError
        | SessionCreated
        | SessionUpdated,
    ) -> None:
        agent = self._configured(event.agent_id)
        if agent is None:
            return
        status = AgentStatus.IDLE
        if isinstance(event, MessageSent) and event.source != agent.configuration().name:
            status = AgentStatus.GENERATING
        await self.update_agent(event.agent_id, status.value)

    def _configured(self, agent_id: UUID) -> Agent[SessionIdentifier] | None:
        try:
            return self.configured_agent(agent_id)
        except RuntimeError:
            return None

class DefaultSidebarStatisticValues:
    """Read-only providers for values shared by every configured agent."""

    def __init__(self, timestamp_formatter: TimestampFormatter | None = None) -> None:
        self._timestamp_formatter = timestamp_formatter or TimestampFormatter()

    async def agent_name(
        self, agent: Agent[SessionIdentifier], _session: SessionMetadata | None
    ) -> str:
        return agent.configuration().name

    async def agent_id(
        self, agent: Agent[SessionIdentifier], _session: SessionMetadata | None
    ) -> UUID:
        return agent.configuration().id

    async def agent_state(
        self, agent: Agent[SessionIdentifier], _session: SessionMetadata | None
    ) -> str:
        return self._display_enum(agent.status())

    async def agent_model(
        self, agent: Agent[SessionIdentifier], _session: SessionMetadata | None
    ) -> str:
        return agent.configuration().model

    async def reasoning_effort(
        self, agent: Agent[SessionIdentifier], _session: SessionMetadata | None
    ) -> str:
        return self._display_enum(agent.configuration().reasoning_effort)

    async def sandbox(
        self, agent: Agent[SessionIdentifier], _session: SessionMetadata | None
    ) -> str:
        return self._display_enum(agent.configuration().sandbox)

    async def workspace(
        self, agent: Agent[SessionIdentifier], _session: SessionMetadata | None
    ) -> str:
        return str(agent.configuration().workspace)

    async def approval_mode(
        self, agent: Agent[SessionIdentifier], _session: SessionMetadata | None
    ) -> str:
        return self._display_enum(agent.configuration().approval_mode)

    async def web_search(
        self, agent: Agent[SessionIdentifier], _session: SessionMetadata | None
    ) -> str:
        return self._display_enum(agent.configuration().web_search)

    async def prompt(
        self, agent: Agent[SessionIdentifier], _session: SessionMetadata | None
    ) -> str:
        return agent.configuration().base_instructions or "Not set"

    async def developer_instructions(
        self, agent: Agent[SessionIdentifier], _session: SessionMetadata | None
    ) -> str:
        return agent.configuration().developer_instructions or "Not set"

    async def mcp_servers(
        self, agent: Agent[SessionIdentifier], _session: SessionMetadata | None
    ) -> str:
        servers = agent.configuration().mcp_servers
        if not servers:
            return "None configured"
        return "; ".join(
            f"{server.name}: {server.description}" for server in servers
        )

    async def session_name(
        self, _agent: Agent[SessionIdentifier], session: SessionMetadata | None
    ) -> str:
        return "No session selected." if session is None else session.session_name

    async def created(
        self, _agent: Agent[SessionIdentifier], session: SessionMetadata | None
    ) -> str:
        return self._timestamp_formatter.format(None if session is None else session.created_at)

    async def last_message(
        self, _agent: Agent[SessionIdentifier], session: SessionMetadata | None
    ) -> str:
        return self._timestamp_formatter.format(
            None if session is None else session.last_message_at
        )

    async def last_update(
        self, _agent: Agent[SessionIdentifier], session: SessionMetadata | None
    ) -> str:
        return self._timestamp_formatter.format(
            None if session is None else session.last_updated_at
        )

    def _display_enum(self, value: Enum | None) -> str:
        return "Default" if value is None else str(value.value)


def create_default_sidebar_statistics() -> tuple[SidebarStatistic, ...]:
    """Create one shared sequence for a UI application launch."""
    values = DefaultSidebarStatisticValues()
    return (
        FixedStatistic("name", "Name", values.agent_name),
        FixedStatistic("id", "ID", values.agent_id),
        AgentStateStatistic(values),
        FixedStatistic("model", "Model", values.agent_model),
        FixedStatistic("reasoning", "Reasoning", values.reasoning_effort),
        FixedStatistic("sandbox", "Sandbox", values.sandbox),
        FixedStatistic("workspace", "Workspace", values.workspace),
        FixedStatistic("approval_mode", "Approval mode", values.approval_mode),
        FixedStatistic("web_search", "Web search", values.web_search),
        FixedStatistic("prompt", "Prompt", values.prompt),
        FixedStatistic("developer_instructions", "Developer instructions", values.developer_instructions),
        FixedStatistic("mcp_servers", "MCP servers", values.mcp_servers),
        FixedStatistic("session_name", "Name", values.session_name, SidebarStatisticCategory.SESSION),
        FixedStatistic("created", "Created", values.created, SidebarStatisticCategory.SESSION),
        FixedStatistic("last_message", "Last message", values.last_message, SidebarStatisticCategory.SESSION),
        FixedStatistic("last_update", "Last update", values.last_update, SidebarStatisticCategory.SESSION),
        TokenUsageStatistic(),
    )


default_sidebar_statistics = create_default_sidebar_statistics()

def format_time(value: datetime | None) -> str:
    """Compatibility entry point for timestamp rendering."""
    return TimestampFormatter().format(value)


__all__ = [
    "AgentStateStatistic",
    "DefaultSidebarStatisticValues",
    "TimestampFormatter",
    "create_default_sidebar_statistics",
    "default_sidebar_statistics",
    "format_time",
]
