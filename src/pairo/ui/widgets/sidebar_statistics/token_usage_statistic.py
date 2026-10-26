"""Textual-event-driven token usage sidebar statistic."""
from __future__ import annotations
from pairo.core.events import AgentTokenUsageUpdated, SessionDeleted
from pairo.ui.pairo_event_piper import on_pairo
from .statistic import SidebarStatistic, SidebarStatisticCategory, SidebarStatisticContext
class TokenUsageStatistic(SidebarStatistic):
    def __init__(self) -> None:
        super().__init__("token_usage", "Token usage", SidebarStatisticCategory.SESSION)
        self._usage_by_session: dict[str, AgentTokenUsageUpdated] = {}
    @on_pairo(AgentTokenUsageUpdated)
    async def receive_usage(self, event: AgentTokenUsageUpdated) -> None:
        context = self._statistic_context
        if context is None or event.agent_id != context.agent.configuration().id:
            return
        self._usage_by_session[event.session_name] = event
        if context.active_session is not None and event.session_name == context.active_session.name:
            await self.refresh()
    @on_pairo(SessionDeleted)
    async def receive_deletion(self, event: SessionDeleted) -> None:
        context = self._statistic_context
        if context is None or event.agent_id != context.agent.configuration().id:
            return
        self._usage_by_session.pop(event.session_name, None)
        if context.active_session is not None and event.session_name == context.active_session.name:
            await self.refresh()
    async def compute(self, context: SidebarStatisticContext) -> str:
        if context.active_session is None:
            return "Unavailable"
        usage = self._usage_by_session.get(context.active_session.name)
        return "Unavailable" if usage is None else f"{usage.total.total_tokens} tokens"
