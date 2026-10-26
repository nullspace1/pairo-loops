"""Agent and current-session information sidebar."""
from collections.abc import Mapping
from uuid import UUID
from textual import on
from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Select, Static
from pairo.core.agent import SessionIdentifier
from pairo.core.clients import ActiveSession, Agent
from pairo.core.events import EventPipeline
from ..models import EventDetailLevel, SessionMetadata
from ..session_repository import SessionNotFoundError, SessionRepository
from .sidebar_field_group import SidebarFieldGroup
from .sidebar_statistics import SidebarStatisticContext, SidebarStatistics, default_sidebar_statistics

class AgentSidebar(Vertical):
    _AGENT_INFORMATION = "agent"
    _SESSION_INFORMATION = "session"
    agent_values: reactive[dict[str, object]] = reactive(dict, recompose=False)
    session_values: reactive[dict[str, object]] = reactive(dict, recompose=False)
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
        self.agent, self._agents, self._repository = agent, agents, repository
        self._detail_level, self._statistics = detail_level, statistics or default_sidebar_statistics(agent)
        self._information_mode = self._AGENT_INFORMATION
        self._active_statistic_session: ActiveSession[SessionIdentifier] | None = None
        self._accepting_statistics = True
    def compose(self) -> ComposeResult:
        yield Static("Information", classes="sidebar-heading")
        yield Select([("Agent information", self._AGENT_INFORMATION), ("Current session", self._SESSION_INFORMATION)], value=self._information_mode, allow_blank=False, classes="sidebar-information-picker")
        with VerticalScroll(classes="sidebar-information-scroll"):
            yield SidebarFieldGroup(classes="sidebar-information")
    async def on_mount(self) -> None:
        context = await self._statistic_context()
        self._active_statistic_session = context.active_session
        await self._statistics.setup(self.app, context)
        await self._refresh_information_fields()
    async def on_unmount(self) -> None:
        self._accepting_statistics = False
        await self._statistics.teardown()
    @on(Select.Changed, ".sidebar-information-picker")
    async def change_information_mode(self, event: Select.Changed) -> None:
        if event.value is not Select.BLANK:
            self._information_mode = str(event.value)
            await self._refresh_information_fields()
    async def activate_statistics(self) -> None:
        """Recompute for the newly selected exact session identity."""
        self.session_values = {}
        context = await self._statistic_context()
        self._active_statistic_session = context.active_session
        await self._statistics.activate(context)

    def save_agent_statistic(self, key: str, value: object) -> None:
        if not self._accepting_statistics:
            return
        self.agent_values = {**self.agent_values, key: value}
    def save_session_statistic(self, key: str, value: object, session: ActiveSession[SessionIdentifier]) -> None:
        if not self._accepting_statistics:
            return
        active = self.agent.current_session()
        if active is session and self._active_statistic_session is session:
            self.session_values = {**self.session_values, key: value}
    async def watch_agent_values(self) -> None:
        if self.is_mounted:
            await self._refresh_information_fields()
    async def watch_session_values(self) -> None:
        if self.is_mounted:
            await self._refresh_information_fields()
    async def _refresh_information_fields(self) -> None:
        statistics = self._statistics.agent_information if self._information_mode == self._AGENT_INFORMATION else self._statistics.current_session
        values = self.agent_values if self._information_mode == self._AGENT_INFORMATION else self.session_values
        fields = tuple((stat.key, stat.label, values[stat.key]) for stat in statistics if stat.key in values and values[stat.key] is not None)
        try:
            await self.query_one(".sidebar-information", SidebarFieldGroup).set_fields(fields)
        except NoMatches:
            return
    async def _statistic_context(self) -> SidebarStatisticContext:
        active = self.agent.current_session()
        metadata: SessionMetadata | None = None
        if active is not None:
            try: metadata = await self._repository.get_session(self.agent.configuration().id, active.name)
            except SessionNotFoundError: pass
        return SidebarStatisticContext(self.agent, active, metadata)
