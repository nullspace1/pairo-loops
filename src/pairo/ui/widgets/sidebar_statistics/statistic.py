"""Types for asynchronously rendered sidebar statistics."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from typing import Protocol, TypeAlias
from uuid import UUID

from pairo.core.agent import Agent, Runner
from pairo.core.events import EventPipeline

from ...models import SessionMetadata


StatisticValue: TypeAlias = Awaitable[object]


@dataclass(frozen=True)
class SidebarStatisticContext:
    """The current sidebar state made available to statistic providers."""

    agent: Agent
    runner: Runner
    event_pipeline: EventPipeline
    session_name: str | None
    session: SessionMetadata | None


class SidebarStatisticProvider(Protocol):
    """A named value that can be rendered in one sidebar category."""

    @property
    def key(self) -> str: ...

    @property
    def label(self) -> str: ...

    def setup(
        self,
        event_pipeline: EventPipeline,
        agents: Mapping[UUID, Agent],
    ) -> None:
        """Connect the statistic to the current application resources."""

    async def statistic(self, context: SidebarStatisticContext) -> object:
        """Return this field's current value."""
