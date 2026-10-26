"""Textual-event-driven token usage sidebar statistic."""
from __future__ import annotations
from pairo.core.events import AgentTokenUsageUpdated, SessionDeleted
from pairo.ui.pairo_event_piper import on_pairo
from uuid import UUID

from .statistic import (
    SidebarStatistic,
    SidebarStatisticCategory,
    SidebarStatisticContext,
)


class TokenUsageStatistic(SidebarStatistic):
    def __init__(self) -> None:
        super().__init__("token_usage", "Token usage", SidebarStatisticCategory.SESSION)
        self._usage_by_session: dict[tuple[UUID, str], AgentTokenUsageUpdated] = {}

    @on_pairo(AgentTokenUsageUpdated)
    async def receive_usage(self, event: AgentTokenUsageUpdated) -> None:
        context = self._context_for(event.agent_id)
        if context is None:
            return
        self._usage_by_session[(event.agent_id, event.session_name)] = event
        if context.active_session is not None and event.session_name == context.active_session.name:
            await self.refresh(context)

    @on_pairo(SessionDeleted)
    async def receive_deletion(self, event: SessionDeleted) -> None:
        if self._context_for(event.agent_id) is None:
            return
        self._usage_by_session.pop((event.agent_id, event.session_name), None)

    async def has_initial_value(self, context: SidebarStatisticContext) -> bool:
        """Keep a durable event value until an event supplies authoritative usage."""
        session_name = context.session_name
        return session_name is not None and (
            context.agent.configuration().id,
            session_name,
        ) in self._usage_by_session

    async def compute(self, context: SidebarStatisticContext) -> str:
        if context.active_session is None:
            return "Unavailable"
        usage = self._usage_by_session.get(
            (context.agent.configuration().id, context.active_session.name)
        )
        return "Unavailable" if usage is None else f"{usage.total.total_tokens} tokens"
