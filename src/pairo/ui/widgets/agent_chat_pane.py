"""Composite workspace for one agent."""

from uuid import UUID

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Input, Select

from pairo.core.agent import Agent, Runner
from pairo.core.events import EventPipeline

from ..models import EventDetailLevel, TranscriptEntry
from ..transcribers import TranscriberRegistry
from ..session_repository import SessionNotFoundError, SessionRepository
from .agent_sidebar import AgentSidebar
from .sidebar_statistics import SidebarStatistics
from .transcript import Transcript


class AgentChatPane(Vertical):
    class DetailLevelChanged(Message):
        def __init__(self, detail_level: EventDetailLevel) -> None:
            super().__init__()
            self.detail_level = detail_level

    def __init__(
        self,
        agent: Agent,
        runner: Runner,
        repository: SessionRepository,
        event_pipeline: EventPipeline,
        transcribers: TranscriberRegistry,
        detail_level: EventDetailLevel,
        *,
        sidebar_statistics: SidebarStatistics | None = None,
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
        self._runner = runner
        self.agent_id = agent.id
        self.session_name = runner.session_name(agent.id)
        self._repository = repository
        self._event_pipeline = event_pipeline
        self._transcribers = transcribers
        self._detail_level = detail_level
        self._sidebar_statistics = sidebar_statistics

    def compose(self) -> ComposeResult:
        with Horizontal(classes="agent-workspace"):
            with Vertical(classes="chat-main"):
                yield Transcript(
                    self.agent_id,
                    self.session_name,
                    self._event_pipeline,
                    self._transcribers,
                    self._detail_level,
                    classes="transcript",
                )
                with Horizontal(classes="chat-input-container"):
                    yield Input(
                        placeholder="Message this agent or enter /help",
                        classes="chat-input",
                    )
                    yield Select(
                        [("Normal", "normal"), ("Debug", "debug")],
                        value=self._detail_level.value,
                        allow_blank=False,
                        classes="detail-filter",
                    )
            yield AgentSidebar(
                self.agent,
                self._runner,
                self._repository,
                self._detail_level,
                self._event_pipeline,
                statistics=self._sidebar_statistics,
                classes="agent-sidebar",
            )

    async def on_mount(self) -> None:
        if self.session_name is not None:
            try:
                entries = await self._repository.entries_for(
                    self.agent_id, self.session_name
                )
            except SessionNotFoundError:
                return
            await self.transcript.replace_entries(entries)

    @on(Select.Changed, ".detail-filter")
    def change_detail_level(self, event: Select.Changed) -> None:
        if event.value is not Select.BLANK:
            level = EventDetailLevel(str(event.value))
            if level != self._detail_level:
                self._detail_level = level
                self.post_message(self.DetailLevelChanged(level))

    @property
    def input(self) -> Input:
        return self.query_one(Input)

    @property
    def transcript(self) -> Transcript:
        return self.query_one(Transcript)

    @property
    def sidebar(self) -> AgentSidebar:
        return self.query_one(AgentSidebar)

    async def add_entry(self, session_name: str, entry: TranscriptEntry) -> None:
        if session_name == self.session_name:
            await self.transcript.add_entry(entry)
        await self.sidebar.refresh_data()

    async def update_entry(self, session_name: str, entry: TranscriptEntry) -> None:
        if session_name == self.session_name:
            await self.transcript.update_entry(entry)
        await self.sidebar.refresh_data()

    async def delete_entry(self, session_name: str, entry_id: UUID) -> None:
        if session_name == self.session_name:
            await self.transcript.delete_entry(entry_id)
        await self.sidebar.refresh_data()

    async def select_session(self, session_name: str) -> None:
        self.session_name = session_name
        entries = await self._repository.entries_for(self.agent_id, session_name)
        self.transcript.set_session(session_name)
        await self.transcript.replace_entries(entries)
        await self.sidebar.refresh_data()

    async def set_detail_level(self, detail_level: EventDetailLevel) -> None:
        self._detail_level = detail_level
        await self.transcript.set_detail_level(detail_level)
        selector = self.query_one(".detail-filter", Select)
        if selector.value != detail_level.value:
            selector.value = detail_level.value

    async def refresh_sidebar(self) -> None:
        await self.sidebar.refresh_data()
