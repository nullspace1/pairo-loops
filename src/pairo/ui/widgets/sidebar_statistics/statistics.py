"""Shared ordered sidebar-statistic definitions."""
from __future__ import annotations

from collections.abc import Sequence

from .statistic import SidebarFieldDefinition, SidebarStatistic, SidebarStatisticCategory


class SidebarStatisticFields:
    """Derive ordered sidebar fields from the shared statistic sequence."""

    def __init__(self, statistics: Sequence[SidebarStatistic]) -> None:
        self._statistics = tuple(statistics)
        keys = tuple(statistic.key for statistic in self._statistics)
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError("Duplicate sidebar statistic keys: " + ", ".join(duplicates))
        owners = tuple(id(statistic) for statistic in self._statistics)
        if len(owners) != len(set(owners)):
            raise ValueError("A sidebar statistic may appear only once.")

    @property
    def agent_fields(self) -> tuple[SidebarFieldDefinition, ...]:
        return self._fields(SidebarStatisticCategory.AGENT)

    @property
    def session_fields(self) -> tuple[SidebarFieldDefinition, ...]:
        return self._fields(SidebarStatisticCategory.SESSION)

    def _fields(
        self, category: SidebarStatisticCategory
    ) -> tuple[SidebarFieldDefinition, ...]:
        return tuple(
            statistic.field
            for statistic in self._statistics
            if statistic.category is category
        )


__all__ = ["SidebarFieldDefinition", "SidebarStatisticFields"]
