"""Composite workspace for one agent."""

from collections.abc import Mapping, Sequence
import asyncio
from dataclasses import dataclass
from uuid import UUID

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Select

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import EventPipeline

from ..models import EventDetailLevel, TranscriptEntry, TranscriptMetadata
from ..transcribers import TranscriberRegistry
from ..session_repository import SessionNotFoundError, SessionRepository
from .agent_sidebar import AgentSidebar
from .chat_editor import ChatEditor
from .sidebar_statistics.statistics import SidebarFieldDefinition
from .transcript import Transcript


class _PendingTranscriptMutation:
    async def apply(self, transcript: Transcript) -> None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _PendingAdd(_PendingTranscriptMutation):
    entry: TranscriptEntry[TranscriptMetadata]

    async def apply(self, transcript: Transcript) -> None:
        await transcript.add_entry(self.entry)


@dataclass(frozen=True, slots=True)
class _PendingUpdate(_PendingTranscriptMutation):
    entry: TranscriptEntry[TranscriptMetadata]

    async def apply(self, transcript: Transcript) -> None:
        await transcript.update_entry(self.entry)


@dataclass(frozen=True, slots=True)
class _PendingDelete(_PendingTranscriptMutation):
    entry_id: UUID

    async def apply(self, transcript: Transcript) -> None:
        await transcript.delete_entry(self.entry_id)


class AgentChatPane(Vertical):
    class DetailLevelChanged(Message):
        def __init__(self, detail_level: EventDetailLevel) -> None:
            super().__init__()
            self.detail_level = detail_level

    def __init__(
        self,
        agent: Agent[SessionIdentifier],
        agents: Mapping[UUID, Agent[SessionIdentifier]],
        repository: SessionRepository,
        event_pipeline: EventPipeline,
        transcribers: TranscriberRegistry,
        detail_level: EventDetailLevel,
        *,
        sidebar_fields: tuple[Sequence[SidebarFieldDefinition], Sequence[SidebarFieldDefinition]],
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
        self.agent_id = agent.configuration().id
        active = agent.current_session()
        self.session_name = active.name if active is not None else None
        self._repository = repository
        self._event_pipeline = event_pipeline
        self._transcribers = transcribers
        self._detail_level = detail_level
        self._sidebar_fields = sidebar_fields
        self._session_generation = 0
        self._pending_mutations: list[_PendingTranscriptMutation] = []
        self._transition_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        with Horizontal(classes="agent-workspace"):
            with Vertical(classes="chat-main"):
                yield Transcript(
                    self.agent_id,
                    self.session_name,
                    self._event_pipeline,
                    self._transcribers,
                    self._detail_level,
                    agent_name=self.agent.configuration().name,
                    classes="transcript",
                )
                with Horizontal(classes="chat-input-container"):
                    yield ChatEditor(
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
                self._sidebar_fields[0],
                self._sidebar_fields[1],
                classes="agent-sidebar",
            )

    async def on_mount(self) -> None:
        if self.session_name is not None:
            async with self._transition_lock:
                session_name = self.session_name
                try:
                    entries = await self._repository.entries_for(self.agent_id, session_name)
                except SessionNotFoundError:
                    return
                if not self.is_mounted or session_name != self.session_name:
                    return
                await self.transcript.populate_entries(entries)
                if self.is_mounted and session_name == self.session_name:
                    await self._apply_pending_mutations()

    @on(Select.Changed, ".detail-filter")
    def change_detail_level(self, event: Select.Changed) -> None:
        if event.value is not Select.BLANK:
            level = EventDetailLevel(str(event.value))
            if level != self._detail_level:
                self._detail_level = level
                self.post_message(self.DetailLevelChanged(level))

    @property
    def input(self) -> ChatEditor:
        return self.query_one(ChatEditor)

    @property
    def transcript(self) -> Transcript:
        return self.query_one(Transcript)

    @property
    def sidebar(self) -> AgentSidebar:
        return self.query_one(AgentSidebar)

    async def add_entry(self, session_name: str, entry: TranscriptEntry[TranscriptMetadata]) -> None:
        await self._apply_or_queue(session_name, _PendingAdd(entry))

    async def update_entry(self, session_name: str, entry: TranscriptEntry[TranscriptMetadata]) -> None:
        await self._apply_or_queue(session_name, _PendingUpdate(entry))

    async def delete_entry(self, session_name: str, entry_id: UUID) -> None:
        await self._apply_or_queue(session_name, _PendingDelete(entry_id))

    async def select_session(self, session_name: str) -> None:
        async with self._transition_lock:
            self.session_name = session_name
            self._session_generation += 1
            self._pending_mutations.clear()
            entries = await self._repository.entries_for(self.agent_id, session_name)
            if not self.is_mounted or session_name != self.session_name:
                return
            await self.transcript.switch_session(session_name, entries)
            if self.is_mounted and session_name == self.session_name:
                await self._apply_pending_mutations()

    async def set_detail_level(self, detail_level: EventDetailLevel) -> None:
        self._detail_level = detail_level
        await self.transcript.set_detail_level(detail_level)
        selector = self.query_one(".detail-filter", Select)
        if selector.value != detail_level.value:
            selector.value = detail_level.value

    async def _apply_or_queue(
        self, session_name: str, mutation: _PendingTranscriptMutation
    ) -> None:
        async with self._transition_lock:
            if not self.is_mounted or session_name != self.session_name:
                return
            if self.transcript.is_displaying_session(session_name):
                await mutation.apply(self.transcript)
                return
            self._pending_mutations.append(mutation)

    async def _apply_pending_mutations(self) -> None:
        pending, self._pending_mutations = self._pending_mutations, []
        for mutation in pending:
            await mutation.apply(self.transcript)
