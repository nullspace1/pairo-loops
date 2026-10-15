"""Configurable statistic providers used by :class:`AgentSidebar`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from inspect import isawaitable
from typing import Protocol, TypeAlias
from uuid import UUID

from pairo.core.agent import Agent, Runner
from pairo.core.events import AgentTokenUsageUpdated, EventPipeline

from ..models import SessionMetadata


StatisticValue: TypeAlias = object | Awaitable[object]
TokenUsageLookup: TypeAlias = Callable[[UUID, str], AgentTokenUsageUpdated | None]


@dataclass(frozen=True)
class SidebarStatisticContext:
    """The current sidebar state made available to statistic providers."""

    agent: Agent
    runner: Runner
    event_pipeline: EventPipeline
    session_name: str | None
    session: SessionMetadata | None


class SidebarStatisticProvider(Protocol):
    """A named value that can be rendered in one sidebar category."""

    @property
    def key(self) -> str: ...

    @property
    def label(self) -> str: ...

    def statistic(self, context: SidebarStatisticContext) -> StatisticValue:
        """Return this field's current value."""


@dataclass(frozen=True)
class SidebarStatistic:
    """A small provider suitable for fixed and callback-backed statistics."""

    key: str
    label: str
    value: Callable[[SidebarStatisticContext], StatisticValue]

    def statistic(self, context: SidebarStatisticContext) -> StatisticValue:
        return self.value(context)


@dataclass(frozen=True)
class TokenUsageStatistic:
    """Render the latest routed token total for the context's agent session."""

    key: str = "token_usage"
    label: str = "Token usage"
    usage_lookup: TokenUsageLookup | None = None

    def statistic(self, context: SidebarStatisticContext) -> str:
        if context.session_name is None:
            return "Unavailable"
        if self.usage_lookup is None:
            return "Unavailable"
        usage = self.usage_lookup(context.agent.id, context.session_name)
        return "Unavailable" if usage is None else f"{usage.total.total_tokens} tokens"


@dataclass(frozen=True)
class SidebarStatistics:
    """Ordered sidebar categories injected into an :class:`AgentSidebar`.

    A configuration is deliberately data-only: adding or removing a statistic
    is a change to this collection, not a change to sidebar rendering code.
    """

    agent_information: tuple[SidebarStatisticProvider, ...]
    current_session: tuple[SidebarStatisticProvider, ...]

    def __post_init__(self) -> None:
        self._validate_keys("agent information", self.agent_information)
        self._validate_keys("current session", self.current_session)

    @staticmethod
    def _validate_keys(
        category: str, statistics: Sequence[SidebarStatisticProvider]
    ) -> None:
        keys = [statistic.key for statistic in statistics]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(
                f"Duplicate sidebar statistic keys in {category}: {', '.join(duplicates)}."
            )


async def statistic_value(
    provider: SidebarStatisticProvider, context: SidebarStatisticContext
) -> object:
    """Resolve synchronous and asynchronous providers through one boundary."""
    value = provider.statistic(context)
    return await value if isawaitable(value) else value


def default_sidebar_statistics(
    agent: Agent,
    token_usage_for_session: TokenUsageLookup | None = None,
) -> SidebarStatistics:
    """Build the legacy fields as the default configurable collection."""

    def enum_value(value: object | None) -> str:
        return "Default" if value is None else str(getattr(value, "value", value))

    agent_statistics: tuple[SidebarStatisticProvider, ...] = (
        SidebarStatistic("name", "Name", lambda context: context.agent.name),
        SidebarStatistic("id", "ID", lambda context: context.agent.id),
        SidebarStatistic(
            "state",
            "State",
            lambda context: getattr(
                context.runner.state(context.agent.id),
                "value",
                context.runner.state(context.agent.id),
            ),
        ),
        SidebarStatistic("model", "Model", lambda context: context.agent.model),
        SidebarStatistic(
            "reasoning", "Reasoning", lambda context: enum_value(context.agent.reasoning_effort)
        ),
        SidebarStatistic("sandbox", "Sandbox", lambda context: enum_value(context.agent.sandbox)),
        SidebarStatistic("workspace", "Workspace", lambda context: context.agent.workspace),
        SidebarStatistic(
            "approval_mode", "Approval mode", lambda context: enum_value(context.agent.approval_mode)
        ),
        SidebarStatistic(
            "web_search", "Web search", lambda context: enum_value(context.agent.web_search)
        ),
        SidebarStatistic("prompt", "Prompt", lambda context: context.agent.base_instructions or "Not set"),
        SidebarStatistic(
            "developer_instructions",
            "Developer instructions",
            lambda context: context.agent.developer_instructions or "Not set",
        ),
    )
    server_statistics = tuple(
        SidebarStatistic(
            f"mcp_server:{server.name}",
            f"MCP server: {server.name}",
            lambda _context, description=server.description: description,
        )
        for server in agent.mcp_servers
    ) or (SidebarStatistic("mcp_servers", "MCP servers", lambda _context: "None configured"),)
    session_statistics: tuple[SidebarStatisticProvider, ...] = (
        SidebarStatistic(
            "session_name",
            "Name",
            lambda context: (
                context.session.session_name
                if context.session is not None
                else context.session_name or "No session selected."
            ),
        ),
        SidebarStatistic(
            "created", "Created", lambda context: format_time(context.session.created_at) if context.session else "None"
        ),
        SidebarStatistic(
            "last_message", "Last message", lambda context: format_time(context.session.last_message_at) if context.session else "None"
        ),
        SidebarStatistic(
            "last_update", "Last update", lambda context: format_time(context.session.last_updated_at) if context.session else "None"
        ),
        SidebarStatistic(
            "duration", "Duration", lambda context: format_duration(context.session.duration_seconds) if context.session else "00:00:00"
        ),
        TokenUsageStatistic(usage_lookup=token_usage_for_session),
    )
    return SidebarStatistics(agent_statistics + server_statistics, session_statistics)


def format_time(value: object | None) -> str:
    if value is None:
        return "None"
    # Session timestamps are timezone-aware datetimes; retaining this narrow
    # conversion keeps custom providers independent of Textual formatting.
    local = value.astimezone()  # type: ignore[union-attr]
    offset = local.strftime("%z")
    readable_offset = f"{offset[:3]}:{offset[3:]}" if offset else ""
    return f"{local:%Y-%m-%d %H:%M:%S} {readable_offset}".rstrip()


def format_duration(seconds: float) -> str:
    whole_seconds = max(0, int(seconds))
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"
