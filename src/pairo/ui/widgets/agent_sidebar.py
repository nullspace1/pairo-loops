"""Render scoped sidebar values owned by one agent pane."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from textual import on
from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Select, Static

from pairo.ui.session_repository import SessionNotFoundError

from .sidebar_field_group import SidebarFieldGroup
from .sidebar_statistics import (
    SidebarStatistic,
    SidebarStatisticCategory,
    SidebarStatisticFields,
    UpdateAgentStatistic,
    UpdateSessionStatistic,
)


class AgentSidebar(Vertical):
    """The sole owner of this pane's visible statistic maps."""

    _AGENT_INFORMATION = "agent"
    _SESSION_INFORMATION = "session"

    agent_values: reactive[dict[str, str]] = reactive(dict, recompose=False)
    session_values: reactive[dict[str, str]] = reactive(dict, recompose=False)

    def __init__(
        self,
        agent_id: UUID,
        statistics: Sequence[SidebarStatistic],
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(
            name=name, id=id, classes=classes, disabled=disabled, markup=markup
        )
        self._agent_id = agent_id
        self._statistics = tuple(statistics)
        fields = SidebarStatisticFields(self._statistics)
        self._agent_fields = fields.agent_fields
        self._session_fields = fields.session_fields
        self._agent_keys = frozenset(field.key for field in self._agent_fields)
        self._session_keys = frozenset(field.key for field in self._session_fields)
        self._session_name: str | None = None
        self._information_mode = self._AGENT_INFORMATION
        self._load_generation = 0
        self._load_lock = asyncio.Lock()

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
        await self._refresh_information_fields()

    async def load(self, session_name: str | None) -> None:
        """Load values through statistics without writing navigation state."""
        async with self._load_lock:
            self._load_generation += 1
            generation = self._load_generation
            self._session_name = session_name
        agent_values, session_values = await asyncio.gather(
            self._values_for(SidebarStatisticCategory.AGENT, session_name),
            self._session_values_for_load(session_name, generation),
        )
        async with self._load_lock:
            if generation != self._load_generation or session_values is None:
                return
            self.agent_values = agent_values
            self.session_values = session_values

    async def _session_values_for_load(
        self, session_name: str | None, generation: int
    ) -> dict[str, str] | None:
        try:
            return await self._values_for(SidebarStatisticCategory.SESSION, session_name)
        except SessionNotFoundError:
            async with self._load_lock:
                if generation != self._load_generation:
                    return None
            raise

    async def _values_for(
        self, category: SidebarStatisticCategory, session_name: str | None
    ) -> dict[str, str]:
        values: dict[str, str] = {}
        for statistic in self._statistics:
            if statistic.category is not category:
                continue
            value = await statistic.value(self._agent_id, session_name)
            if value is not None:
                values[statistic.key] = value
        return values

    @on(UpdateAgentStatistic)
    def update_agent(self, message: UpdateAgentStatistic) -> None:
        if message.agent_id != self._agent_id or message.key not in self._agent_keys:
            return
        self.agent_values = self._merged(self.agent_values, message.key, message.value)

    @on(UpdateSessionStatistic)
    def update_session(self, message: UpdateSessionStatistic) -> None:
        if (
            message.agent_id != self._agent_id
            or message.session_name != self._session_name
            or message.key not in self._session_keys
        ):
            return
        self.session_values = self._merged(
            self.session_values, message.key, message.value
        )

    def _merged(
        self, values: dict[str, str], key: str, value: str | None
    ) -> dict[str, str]:
        result = dict(values)
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
        return result

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
        fields = (
            self._agent_fields
            if self._information_mode == self._AGENT_INFORMATION
            else self._session_fields
        )
        values = (
            self.agent_values
            if self._information_mode == self._AGENT_INFORMATION
            else self.session_values
        )
        visible = tuple(
            (field.key, field.label, values[field.key])
            for field in fields
            if field.key in values
        )
        try:
            await self.query_one(".sidebar-information", SidebarFieldGroup).set_fields(
                visible
            )
        except NoMatches:
            return
