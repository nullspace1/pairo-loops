"""Pipeline-backed token-usage sidebar statistic."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import (
    AgentTokenUsageUpdated,
    EventPipeline,
    SessionDeleted,
    Subscription,
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
    _event_pipeline: EventPipeline | None = field(default=None, init=False, repr=False)
    _usage_subscription: Subscription[AgentTokenUsageUpdated] | None = field(
        default=None, init=False, repr=False
    )
    _deletion_subscription: Subscription[SessionDeleted] | None = field(
        default=None, init=False, repr=False
    )

    def setup(
        self,
        event_pipeline: EventPipeline,
        agents: Mapping[UUID, Agent[SessionIdentifier]],
    ) -> None:
        """Register future-only token and session observations."""
        self._event_pipeline = event_pipeline
        self._usage_subscription = event_pipeline.subscribe(
            AgentTokenUsageUpdated,
            self._record_usage,
        )
        self._deletion_subscription = event_pipeline.subscribe(
            SessionDeleted,
            self._forget_usage,
        )

    def teardown(self) -> None:
        pipeline = self._event_pipeline
        if pipeline is None:
            return
        if self._usage_subscription is not None:
            pipeline.unsubscribe(self._usage_subscription)
        if self._deletion_subscription is not None:
            pipeline.unsubscribe(self._deletion_subscription)
        self._usage_subscription = None
        self._deletion_subscription = None
        self._event_pipeline = None

    def _record_usage(
        self,
        event: AgentTokenUsageUpdated,
        _subscription: Subscription[AgentTokenUsageUpdated],
    ) -> None:
        self._usage_by_session[(event.agent_id, event.session_name)] = event

    def _forget_usage(
        self,
        event: SessionDeleted,
        _subscription: Subscription[SessionDeleted],
    ) -> None:
        """Avoid showing a deleted session's usage if its name is reused."""
        self._usage_by_session.pop((event.agent_id, event.session_name), None)

    async def statistic(self, context: SidebarStatisticContext) -> str:
        if context.session_name is None:
            return "Unavailable"
        usage = self._usage_by_session.get(
            (context.agent.configuration().id, context.session_name)
        )
        return "Unavailable" if usage is None else f"{usage.total.total_tokens} tokens"

