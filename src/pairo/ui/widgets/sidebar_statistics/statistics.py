"""Collections of sidebar statistic providers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from pairo.core.agent import Agent
from pairo.core.events import EventPipeline

from .statistic import SidebarStatisticProvider


@dataclass(frozen=True)
class SidebarStatistics:
    """Ordered sidebar categories injected into an agent sidebar."""

    agent_information: tuple[SidebarStatisticProvider, ...]
    current_session: tuple[SidebarStatisticProvider, ...]
    _event_pipeline: EventPipeline | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_keys("agent information", self.agent_information)
        self._validate_keys("current session", self.current_session)

    def _validate_keys(
        self,
        category: str,
        statistics: Sequence[SidebarStatisticProvider],
    ) -> None:
        keys = [statistic.key for statistic in statistics]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(
                f"Duplicate sidebar statistic keys in {category}: {', '.join(duplicates)}."
            )

    def setup(
        self,
        event_pipeline: EventPipeline,
        agents: Mapping[UUID, Agent],
    ) -> None:
        """Configure every provider once for this application event pipeline."""
        if self._event_pipeline is event_pipeline:
            return
        if self._event_pipeline is not None:
            raise RuntimeError("Sidebar statistics are already configured.")
        for statistic in self.agent_information + self.current_session:
            statistic.setup(event_pipeline, agents)
        object.__setattr__(self, "_event_pipeline", event_pipeline)

