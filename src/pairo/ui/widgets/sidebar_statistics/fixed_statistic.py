"""Callback-backed static sidebar statistic."""
from __future__ import annotations
from collections.abc import Callable, Awaitable
from .statistic import SidebarStatistic, SidebarStatisticCategory, SidebarStatisticContext
class FixedStatistic(SidebarStatistic):
    def __init__(self, key: str, label: str, value: Callable[[SidebarStatisticContext], Awaitable[object]], category: SidebarStatisticCategory = SidebarStatisticCategory.AGENT) -> None:
        super().__init__(key, label, category)
        self._value = value
    async def compute(self, context: SidebarStatisticContext) -> object:
        return await self._value(context)
