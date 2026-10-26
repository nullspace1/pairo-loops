"""Render-only agent and current-session information sidebar."""
from collections.abc import Sequence

from textual import on
from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Select, Static

from .sidebar_field_group import SidebarFieldGroup
from .sidebar_statistics.statistics import SidebarFieldDefinition


class AgentSidebar(Vertical):
    _AGENT_INFORMATION = "agent"
    _SESSION_INFORMATION = "session"
    agent_values: reactive[dict[str, str]] = reactive(dict, recompose=False)
    session_values: reactive[dict[str, str]] = reactive(dict, recompose=False)

    def __init__(
        self,
        agent_fields: Sequence[SidebarFieldDefinition],
        session_fields: Sequence[SidebarFieldDefinition],
        *,
        name: str | None = None, id: str | None = None, classes: str | None = None,
        disabled: bool = False, markup: bool = True,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled, markup=markup)
        self._agent_fields = tuple(agent_fields)
        self._session_fields = tuple(session_fields)
        self._information_mode = self._AGENT_INFORMATION

    def compose(self) -> ComposeResult:
        yield Static("Information", classes="sidebar-heading")
        yield Select([("Agent information", self._AGENT_INFORMATION), ("Current session", self._SESSION_INFORMATION)], value=self._information_mode, allow_blank=False, classes="sidebar-information-picker")
        with VerticalScroll(classes="sidebar-information-scroll"):
            yield SidebarFieldGroup(classes="sidebar-information")

    async def on_mount(self) -> None:
        await self._refresh_information_fields()

    @on(Select.Changed, ".sidebar-information-picker")
    async def change_information_mode(self, event: Select.Changed) -> None:
        if event.value is not Select.BLANK:
            self._information_mode = str(event.value)
            await self._refresh_information_fields()

    async def watch_agent_values(self) -> None:
        if self.is_mounted:
            await self._refresh_information_fields()

    async def watch_session_values(self) -> None:
        if self.is_mounted:
            await self._refresh_information_fields()

    async def _refresh_information_fields(self) -> None:
        fields = self._agent_fields if self._information_mode == self._AGENT_INFORMATION else self._session_fields
        values = self.agent_values if self._information_mode == self._AGENT_INFORMATION else self.session_values
        visible = tuple((field.key, field.label, values[field.key]) for field in fields if field.key in values and values[field.key] is not None)
        try:
            await self.query_one(".sidebar-information", SidebarFieldGroup).set_fields(visible)
        except NoMatches:
            return
