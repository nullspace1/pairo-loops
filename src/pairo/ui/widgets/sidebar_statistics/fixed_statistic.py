"""Callback-backed sidebar statistics."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from pairo.core.agent import Agent
from pairo.core.events import EventPipeline

from .statistic import SidebarStatisticContext, StatisticValue


@dataclass(frozen=True)
class FixedStatistic:
    """A named sidebar value supplied by one asynchronous callback."""

    key: str
    label: str
    value: Callable[[SidebarStatisticContext], StatisticValue]

    def setup(
        self,
        event_pipeline: EventPipeline,
        agents: Mapping[UUID, Agent],
    ) -> None:
        """Satisfy the provider lifecycle; fixed values need no resources."""

    async def statistic(self, context: SidebarStatisticContext) -> object:
        return await self.value(context)

