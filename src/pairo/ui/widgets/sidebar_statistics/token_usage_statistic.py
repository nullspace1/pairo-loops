"""Pipeline-backed token-usage sidebar statistic."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from pairo.core.agent import Agent
from pairo.core.events import (
    AgentTokenUsageUpdated,
    EventPipeline,
    SessionDeleted,
)

from .statistic import SidebarStatisticContext


@dataclass
class TokenUsageStatistic:
    """Track token-use events published to an event pipeline."""

    key: str = "token_usage"
    label: str = "Token usage"
    _usage_by_session: dict[tuple[UUID, str], AgentTokenUsageUpdated] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def setup(
        self,
        event_pipeline: EventPipeline,
        agents: Mapping[UUID, Agent],
    ) -> None:
        """Register once and replay pipeline history for immediately useful values."""
        self._event_pipeline = event_pipeline
        event_pipeline.add_listener(
            AgentTokenUsageUpdated,
            self._record_usage,
            process_existing_events=True,
        )
        event_pipeline.add_listener(SessionDeleted, self._forget_usage)

    def _record_usage(self, event: AgentTokenUsageUpdated) -> AgentTokenUsageUpdated:
        self._usage_by_session[(event.agent_id, event.session_name)] = event
        return event

    def _forget_usage(self, event: SessionDeleted) -> SessionDeleted:
        """Avoid showing a deleted session's usage if its name is reused."""
        self._usage_by_session.pop((event.agent_id, event.session_name), None)
        return event

    async def statistic(self, context: SidebarStatisticContext) -> str:
        if context.session_name is None:
            return "Unavailable"
        usage = self._usage_by_session.get((context.agent.id, context.session_name))
        return "Unavailable" if usage is None else f"{usage.total.total_tokens} tokens"

