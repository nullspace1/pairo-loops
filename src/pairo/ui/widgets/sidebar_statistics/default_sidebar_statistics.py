"""Default sidebar statistic configuration and display formatters."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent

from .fixed_statistic import FixedStatistic
from .statistic import SidebarStatisticCategory, SidebarStatisticContext, SidebarStatistic
from .statistics import SidebarStatistics
from .token_usage_statistic import TokenUsageStatistic



class TimestampFormatter:
    """Render optional timestamps in the sidebar's local-time format."""

    def format(self, value: datetime | None) -> str:
        """Return a readable local timestamp or the legacy empty-value label."""
        if value is None:
            return "None"
        local = value.astimezone()
        offset = local.strftime("%z")
        readable_offset = f"{offset[:3]}:{offset[3:]}" if offset else ""
        return f"{local:%Y-%m-%d %H:%M:%S} {readable_offset}".rstrip()


class ConstantStatisticValue:
    """Provide a constant asynchronous statistic value without callback closures."""

    def __init__(self, value: object) -> None:
        self._value = value

    async def __call__(self, _context: SidebarStatisticContext) -> object:
        return self._value


class DefaultSidebarStatisticsFactory:
    """Build the standard agent and session sidebar statistic collections."""

    def __init__(
        self,
        agent: Agent[SessionIdentifier],
        timestamp_formatter: TimestampFormatter | None = None,
    ) -> None:
        self._agent = agent
        self._configuration = agent.configuration()
        self._timestamp_formatter = timestamp_formatter or TimestampFormatter()

    def build(self) -> SidebarStatistics:
        """Compose the default agent, MCP-server, and session statistic providers."""
        return SidebarStatistics(
            self._agent_statistics() + self._server_statistics(),
            self._session_statistics(),
        )

    def _agent_statistics(self) -> tuple[SidebarStatistic, ...]:
        return (
            FixedStatistic("name", "Name", self._agent_name),
            FixedStatistic("id", "ID", self._agent_id),
            FixedStatistic("state", "State", self._agent_state),
            FixedStatistic("model", "Model", self._agent_model),
            FixedStatistic("reasoning", "Reasoning", self._reasoning_effort),
            FixedStatistic("sandbox", "Sandbox", self._sandbox),
            FixedStatistic("workspace", "Workspace", self._workspace),
            FixedStatistic("approval_mode", "Approval mode", self._approval_mode),
            FixedStatistic("web_search", "Web search", self._web_search),
            FixedStatistic("prompt", "Prompt", self._prompt),
            FixedStatistic(
                "developer_instructions",
                "Developer instructions",
                self._developer_instructions,
            ),
        )

    def _server_statistics(self) -> tuple[SidebarStatistic, ...]:
        statistics = tuple(
            FixedStatistic(
                f"mcp_server:{server.name}",
                f"MCP server: {server.name}",
                ConstantStatisticValue(server.description),
            )
            for server in self._configuration.mcp_servers
        )
        return statistics or (
            FixedStatistic(
                "mcp_servers",
                "MCP servers",
                ConstantStatisticValue("None configured"),
            ),
        )

    def _session_statistics(self) -> tuple[SidebarStatistic, ...]:
        return (
            FixedStatistic("session_name", "Name", self._session_name, SidebarStatisticCategory.SESSION),
            FixedStatistic("created", "Created", self._created, SidebarStatisticCategory.SESSION),
            FixedStatistic("last_message", "Last message", self._last_message, SidebarStatisticCategory.SESSION),
            FixedStatistic("last_update", "Last update", self._last_update, SidebarStatisticCategory.SESSION),
            TokenUsageStatistic(),
        )

    async def _agent_name(self, context: SidebarStatisticContext) -> str:
        return context.agent.configuration().name

    async def _agent_id(self, context: SidebarStatisticContext) -> UUID:
        return context.agent.configuration().id

    async def _agent_state(self, context: SidebarStatisticContext) -> str:
        return self._display_enum(context.agent.status())

    async def _agent_model(self, context: SidebarStatisticContext) -> str:
        return context.agent.configuration().model

    async def _reasoning_effort(self, context: SidebarStatisticContext) -> str:
        return self._display_enum(context.agent.configuration().reasoning_effort)

    async def _sandbox(self, context: SidebarStatisticContext) -> str:
        return self._display_enum(context.agent.configuration().sandbox)

    async def _workspace(self, context: SidebarStatisticContext) -> str:
        return str(context.agent.configuration().workspace)

    async def _approval_mode(self, context: SidebarStatisticContext) -> str:
        return self._display_enum(context.agent.configuration().approval_mode)

    async def _web_search(self, context: SidebarStatisticContext) -> str:
        return self._display_enum(context.agent.configuration().web_search)

    async def _prompt(self, context: SidebarStatisticContext) -> str:
        return context.agent.configuration().base_instructions or "Not set"

    async def _developer_instructions(
        self,
        context: SidebarStatisticContext,
    ) -> str:
        return context.agent.configuration().developer_instructions or "Not set"

    async def _session_name(self, context: SidebarStatisticContext) -> str:
        if context.session is not None:
            return context.session.session_name
        return context.session_name or "No session selected."

    async def _created(self, context: SidebarStatisticContext) -> str:
        return self._timestamp_formatter.format(
            context.session.created_at if context.session else None
        )

    async def _last_message(self, context: SidebarStatisticContext) -> str:
        return self._timestamp_formatter.format(
            context.session.last_message_at if context.session else None
        )

    async def _last_update(self, context: SidebarStatisticContext) -> str:
        return self._timestamp_formatter.format(
            context.session.last_updated_at if context.session else None
        )


    def _display_enum(self, value: Enum | None) -> str:
        return "Default" if value is None else str(value.value)


class DefaultSidebarStatistics:
    """Callable public entry point for standard statistics."""

    def __call__(self, agent: Agent[SessionIdentifier]) -> SidebarStatistics:
        return DefaultSidebarStatisticsFactory(agent).build()


default_sidebar_statistics = DefaultSidebarStatistics()


def format_time(value: datetime | None) -> str:
    """Compatibility entry point for timestamp rendering."""
    return TimestampFormatter().format(value)



__all__ = [
    "ConstantStatisticValue",
    "DefaultSidebarStatistics",
    "DefaultSidebarStatisticsFactory",
    "TimestampFormatter",
    "default_sidebar_statistics",
    "format_time",
]
