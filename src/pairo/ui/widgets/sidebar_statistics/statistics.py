"""Collections of Textual sidebar statistics."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Sequence
from textual.app import App
from .statistic import SidebarStatistic, SidebarStatisticContext

@dataclass(frozen=True)
class SidebarStatistics:
    agent_information: tuple[SidebarStatistic, ...]
    current_session: tuple[SidebarStatistic, ...]
    def __post_init__(self) -> None:
        self._validate("agent information", self.agent_information)
        self._validate("current session", self.current_session)
    def _validate(self, category: str, statistics: Sequence[SidebarStatistic]) -> None:
        keys = [statistic.key for statistic in statistics]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"Duplicate sidebar statistic keys in {category}: {', '.join(duplicates)}.")
    async def setup(self, app: App[None], context: SidebarStatisticContext) -> None:
        for statistic in self.agent_information + self.current_session:
            await statistic.setup(app, context)
    async def activate(self, context: SidebarStatisticContext) -> None:
        for statistic in self.agent_information + self.current_session:
            await statistic.activate(context)
    async def teardown(self) -> None:
        for statistic in self.agent_information + self.current_session:
            await statistic.teardown()

    def copy_for_sidebar(self) -> SidebarStatistics:
        """Create independent message pumps when one configuration serves agents."""
        return deepcopy(self)
    def post(self, message: object) -> None:
        for statistic in self.agent_information + self.current_session:
            if hasattr(message, "_prevent"):
                statistic.post_message(message)  # type: ignore[arg-type]
