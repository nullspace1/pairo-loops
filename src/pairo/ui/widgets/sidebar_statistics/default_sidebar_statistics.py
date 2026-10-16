"""Default sidebar statistic configuration and display formatters."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pairo.core.agent import Agent

from .fixed_statistic import FixedStatistic
from .statistic import SidebarStatisticContext, SidebarStatisticProvider
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


class DurationFormatter:
    """Render elapsed seconds as a non-negative hours/minutes/seconds value."""

    def format(self, seconds: float) -> str:
        """Return the legacy ``HH:MM:SS`` representation."""
        whole_seconds = max(0, int(seconds))
        hours, remainder = divmod(whole_seconds, 3600)
        minutes, remaining_seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"


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
        agent: Agent,
        timestamp_formatter: TimestampFormatter | None = None,
        duration_formatter: DurationFormatter | None = None,
    ) -> None:
        self._agent = agent
        self._timestamp_formatter = timestamp_formatter or TimestampFormatter()
        self._duration_formatter = duration_formatter or DurationFormatter()

    def build(self) -> SidebarStatistics:
        """Compose the default agent, MCP-server, and session statistic providers."""
        return SidebarStatistics(
            self._agent_statistics() + self._server_statistics(),
            self._session_statistics(),
        )

    def _agent_statistics(self) -> tuple[SidebarStatisticProvider, ...]:
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

    def _server_statistics(self) -> tuple[SidebarStatisticProvider, ...]:
        statistics = tuple(
            FixedStatistic(
                f"mcp_server:{server.name}",
                f"MCP server: {server.name}",
                ConstantStatisticValue(server.description),
            )
            for server in self._agent.mcp_servers
        )
        return statistics or (
            FixedStatistic(
                "mcp_servers",
                "MCP servers",
                ConstantStatisticValue("None configured"),
            ),
        )

    def _session_statistics(self) -> tuple[SidebarStatisticProvider, ...]:
        return (
            FixedStatistic("session_name", "Name", self._session_name),
            FixedStatistic("created", "Created", self._created),
            FixedStatistic("last_message", "Last message", self._last_message),
            FixedStatistic("last_update", "Last update", self._last_update),
            FixedStatistic("duration", "Duration", self._duration),
            TokenUsageStatistic(),
        )

    async def _agent_name(self, context: SidebarStatisticContext) -> str:
        return context.agent.name

    async def _agent_id(self, context: SidebarStatisticContext) -> UUID:
        return context.agent.id

    async def _agent_state(self, context: SidebarStatisticContext) -> str:
        return self._display_enum(context.runner.state(context.agent.id))

    async def _agent_model(self, context: SidebarStatisticContext) -> str:
        return context.agent.model

    async def _reasoning_effort(self, context: SidebarStatisticContext) -> str:
        return self._display_enum(context.agent.reasoning_effort)

    async def _sandbox(self, context: SidebarStatisticContext) -> str:
        return self._display_enum(context.agent.sandbox)

    async def _workspace(self, context: SidebarStatisticContext) -> str:
        return str(context.agent.workspace)

    async def _approval_mode(self, context: SidebarStatisticContext) -> str:
        return self._display_enum(context.agent.approval_mode)

    async def _web_search(self, context: SidebarStatisticContext) -> str:
        return self._display_enum(context.agent.web_search)

    async def _prompt(self, context: SidebarStatisticContext) -> str:
        return context.agent.base_instructions or "Not set"

    async def _developer_instructions(
        self,
        context: SidebarStatisticContext,
    ) -> str:
        return context.agent.developer_instructions or "Not set"

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

    async def _duration(self, context: SidebarStatisticContext) -> str:
        if context.session is None:
            return self._duration_formatter.format(0)
        return self._duration_formatter.format(context.session.duration_seconds)

    def _display_enum(self, value: Enum | None) -> str:
        return "Default" if value is None else str(value.value)


def default_sidebar_statistics(agent: Agent) -> SidebarStatistics:
    """Compatibility entry point for the standard sidebar statistics."""
    return DefaultSidebarStatisticsFactory(agent).build()


def format_time(value: datetime | None) -> str:
    """Compatibility entry point for timestamp rendering."""
    return TimestampFormatter().format(value)


def format_duration(seconds: float) -> str:
    """Compatibility entry point for duration rendering."""
    return DurationFormatter().format(seconds)


__all__ = [
    "ConstantStatisticValue",
    "DefaultSidebarStatisticsFactory",
    "DurationFormatter",
    "TimestampFormatter",
    "default_sidebar_statistics",
    "format_duration",
    "format_time",
]

