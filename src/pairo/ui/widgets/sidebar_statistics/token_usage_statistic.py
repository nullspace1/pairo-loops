"""Event-driven durable token-usage sidebar statistic."""
from __future__ import annotations

from uuid import UUID

from pairo.core.events import AgentTokenUsageUpdated, SessionDeleted
from pairo.ui.pairo_event_piper import on_pairo
from pairo.ui.session_repository import SessionNotFoundError

from .statistic import SidebarStatistic, SidebarStatisticCategory


class TokenUsageStatistic(SidebarStatistic):
    def __init__(self) -> None:
        super().__init__("token_usage", "Token usage", SidebarStatisticCategory.SESSION)

    @on_pairo(AgentTokenUsageUpdated)
    async def receive_usage(self, event: AgentTokenUsageUpdated) -> None:
        try:
            await self.update_session(
                event.agent_id,
                event.session_name,
                f"{event.total.total_tokens} tokens",
            )
        except (RuntimeError, SessionNotFoundError):
            return

    @on_pairo(SessionDeleted)
    async def receive_deletion(self, event: SessionDeleted) -> None:
        try:
            await self.update_session(event.agent_id, event.session_name, None)
        except (RuntimeError, SessionNotFoundError):
            return

    async def value(self, agent_id: UUID, session_name: str | None) -> str | None:
        value = await super().value(agent_id, session_name)
        return "Unavailable" if value is None and session_name is not None else value
