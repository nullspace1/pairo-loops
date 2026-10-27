"""Callback-backed fixed sidebar statistic."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.ui.models import SessionMetadata
from pairo.ui.session_repository import SessionNotFoundError

from .statistic import SidebarStatistic, SidebarStatisticCategory

FixedStatisticValue = Callable[
    [Agent[SessionIdentifier], SessionMetadata | None], Awaitable[object]
]


class FixedStatistic(SidebarStatistic):
    """A read-only value calculated from configured agent/session state."""

    def __init__(
        self,
        key: str,
        label: str,
        value: FixedStatisticValue,
        category: SidebarStatisticCategory = SidebarStatisticCategory.AGENT,
    ) -> None:
        super().__init__(key, label, category)
        self._fixed_value = value

    async def value(self, agent_id: UUID, session_name: str | None) -> str | None:
        agent = self.configured_agent(agent_id)
        metadata = await self._session_metadata(agent_id, session_name)
        return str(await self._fixed_value(agent, metadata))

    async def _session_metadata(
        self, agent_id: UUID, session_name: str | None
    ) -> SessionMetadata | None:
        if self.category is SidebarStatisticCategory.AGENT or session_name is None:
            return None
        try:
            return await self._require_repository().get_session(agent_id, session_name)
        except SessionNotFoundError:
            return None
