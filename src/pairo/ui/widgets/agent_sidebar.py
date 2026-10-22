"""Agent and current-session information sidebar."""
from collections.abc import Mapping
from uuid import UUID

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Select, Static

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import EventPipeline

from ..models import EventDetailLevel
from ..session_repository import SessionNotFoundError, SessionRepository
from .sidebar_field_group import SidebarFieldGroup
from .sidebar_statistics import (
    SidebarStatisticContext,
    SidebarStatistics,
    default_sidebar_statistics,
)


class AgentSidebar(Vertical):
    _AGENT_INFORMATION = "agent"
    _SESSION_INFORMATION = "session"

    def __init__(
        self,
        agent: Agent[SessionIdentifier],
        agents: Mapping[UUID, Agent[SessionIdentifier]],
        repository: SessionRepository,
        detail_level: EventDetailLevel,
        event_pipeline: EventPipeline,
        *,
        statistics: SidebarStatistics | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            markup=markup,
        )
        self.agent = agent
        self._agents = agents
        self._repository = repository
        self._detail_level = detail_level
        self._event_pipeline = event_pipeline
        self._statistics = statistics or default_sidebar_statistics(agent)
        self._statistics.setup(event_pipeline, agents)
        self._information_mode = self._AGENT_INFORMATION

    def compose(self) -> ComposeResult:
        yield Static("Information", classes="sidebar-heading")
        yield Select(
            [
                ("Agent information", self._AGENT_INFORMATION),
                ("Current session", self._SESSION_INFORMATION),
            ],
            value=self._information_mode,
            allow_blank=False,
            classes="sidebar-information-picker",
        )
        with VerticalScroll(classes="sidebar-information-scroll"):
            yield SidebarFieldGroup(classes="sidebar-information")

    async def on_mount(self) -> None:
        await self.refresh_data()

    def on_unmount(self) -> None:
        self._statistics.teardown()

    @on(Select.Changed, ".sidebar-information-picker")
    async def change_information_mode(self, event: Select.Changed) -> None:
        if event.value is not Select.BLANK:
            self._information_mode = str(event.value)
            await self._refresh_information_fields()

    async def refresh_data(self) -> None:
        await self._refresh_information_fields()

    async def _refresh_information_fields(self) -> None:
        statistics = (
            self._statistics.agent_information
            if self._information_mode == self._AGENT_INFORMATION
            else self._statistics.current_session
        )
        context = await self._statistic_context(
            include_session=self._information_mode == self._SESSION_INFORMATION
        )
        fields_list = [
            (statistic.key, statistic.label, await statistic.statistic(context))
            for statistic in statistics
        ]
        fields_tuple = tuple(
            (key, label, value) for key, label, value in fields_list if value is not None
        )
        await self.query_one(".sidebar-information", SidebarFieldGroup).set_fields(fields_tuple)

    async def _statistic_context(
        self, *, include_session: bool
    ) -> SidebarStatisticContext:
        configuration = self.agent.configuration()
        active = self.agent.current_session()
        session_name = active.name if active is not None else None
        if session_name is None or not include_session:
            return SidebarStatisticContext(
                self.agent,
                self._event_pipeline,
                None,
                None,
            )
        try:
            session = await self._repository.get_session(configuration.id, session_name)
        except SessionNotFoundError:
            session = None
        return SidebarStatisticContext(
            self.agent,
            self._event_pipeline,
            session_name,
            session,
        )
