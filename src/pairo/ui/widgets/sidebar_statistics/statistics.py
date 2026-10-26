"""Collections of app-owned sidebar statistics."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Sequence
from textual.app import App

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.ui.session_repository import SessionRepository
from .statistic import SidebarStatistic, SidebarStatisticContext


@dataclass(frozen=True, slots=True)
class SidebarFieldDefinition:
    key: str
    label: str


@dataclass(frozen=True)
class SidebarStatistics:
    agent_information: tuple[SidebarStatistic, ...]
    current_session: tuple[SidebarStatistic, ...]

    def __post_init__(self) -> None:
        all_statistics = self.agent_information + self.current_session
        keys = [statistic.key for statistic in all_statistics]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError("Duplicate sidebar statistic keys (duplicate ownership): " + ", ".join(duplicates))
        owners = [id(statistic) for statistic in all_statistics]
        if len(owners) != len(set(owners)):
            raise ValueError("A sidebar statistic may have only one owner.")

    @property
    def agent_fields(self) -> tuple[SidebarFieldDefinition, ...]:
        return tuple(SidebarFieldDefinition(item.key, item.label) for item in self.agent_information)

    @property
    def session_fields(self) -> tuple[SidebarFieldDefinition, ...]:
        return tuple(SidebarFieldDefinition(item.key, item.label) for item in self.current_session)

    async def configure(
        self,
        repository: SessionRepository,
        app: App[None],
        agents: Iterable[Agent[SessionIdentifier]],
    ) -> None:
        configured_agents = tuple(agents)
        for statistic in self.agent_information + self.current_session:
            await statistic.configure(repository, app, configured_agents)

    async def build(self, context: SidebarStatisticContext) -> None:
        for statistic in self.agent_information + self.current_session:
            await statistic.build(context)

    async def teardown(self) -> None:
        for statistic in self.agent_information + self.current_session:
            await statistic.teardown()

    def post(self, message: object) -> None:
        for statistic in self.agent_information + self.current_session:
            if hasattr(message, "_prevent"):
                statistic.post_message(message)  # type: ignore[arg-type]
