"""Textual application for operating asynchronous provider-neutral agents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar, cast
from uuid import UUID

from pydantic import BaseModel
from textual import on
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, TabbedContent, TabPane

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import (
    EventCallback,
    EventPipeline,
    SessionCreated,
    SessionDeleted,
    Subscription,
)

from .commands import AgentCommandController
from .models import (
    EventDetailLevel,
    SystemTranscriptMetadata,
    TranscriptEntry,
    TranscriptMetadata,
)
from .session_repository import SessionRepository
from .sqlite_session_repository import SqliteSessionRepository
from .transcript_service import TranscriptService
from .transcript_events import (
    TranscriptEntryAdded,
    TranscriptEntryDeleted,
    TranscriptEntryUpdated,
)
from .transcribers import (
    TranscriberRegistry,
    default_transcribers,
)
from .widgets import (
    AgentChatPane,
)
from .widgets.chat_editor import ChatEditor
from .widgets.sidebar_statistics import SidebarStatistics, default_sidebar_statistics


AppEventT = TypeVar("AppEventT", bound=BaseModel)


class AgentChatApp(App[None]):
    TITLE = "pairo-loops"
    SUB_TITLE = "Multi-agent console"
    CSS = """
    Screen { layout: vertical; }
    #agent-tabs, AgentChatPane, .agent-workspace { height: 1fr; }
    .chat-main { width: 1fr; }
    .agent-sidebar {
        width: 38; height: 1fr; min-width: 28;
        border-left: solid $primary-background; padding: 0 1;
    }
    .sidebar-heading {
        height: 1; margin-top: 1; margin-bottom: 1;
        text-style: bold; color: $accent;
    }
    .sidebar-information { width: 1fr; height: auto; }
    .sidebar-information-scroll { height: 1fr; min-height: 3; }
    .sidebar-information-picker { width: 1fr; height: 3; margin-bottom: 1; }
    .sidebar-field {
        height: auto; border: round $primary-background; background: $surface;
    }
    .sidebar-field-label { height: 1; text-style: bold; color: $accent; }
    .sidebar-field-value { height: auto; }
    .detail-filter { width: 14; min-width: 14; height: 3; margin-left: 1; }
    .chat-input-container { layout: horizontal; height: 6; margin: 1 0; }
    .transcript {
        height: 1fr; border: round $primary-background; padding: 0 1;
    }
    .transcript-message {
        height: auto; max-height: 12; margin: 1 0;
        border: round $primary-background; padding: 0 1;
    }
    .permission-card {
        height: auto; margin: 1 0; border: heavy $error; padding: 0 1;
    }
    .permission-details { height: auto; }
    .permission-heading { height: auto; text-style: bold; }
    .permission-field { height: auto; margin: 1 0 0 0; }
    .permission-field-label { height: auto; text-style: bold; }
    .permission-field-value { height: auto; width: 1fr; }
    .permission-row { height: auto; padding-left: 2; }
    .permission-actions { height: 3; }
    .permission-actions Button { width: 1fr; min-width: 16; }
    .permission-decision { height: auto; color: $text-muted; text-style: bold; }
    .chat-input { width: 1fr; height: 6; }
    .completed-activity { height: auto; margin: 1 0; }
    .session-table-message { height: auto; margin: 1 0; }
    """
    BINDINGS = [("ctrl+q", "quit", "Quit")]


    def __init__(
        self,
        agents: Mapping[UUID, Agent[SessionIdentifier]],
        event_pipeline: EventPipeline,
        session_repository: SessionRepository | None = None,
        *,
        transcribers: TranscriberRegistry | None = None,
        sidebar_statistics: SidebarStatistics | None = None,
    ) -> None:
        super().__init__()
        agents_by_id = dict(agents)
        self._agents = agents_by_id
        self._event_pipeline = event_pipeline
        self._owns_repository = session_repository is None
        self.session_repository = session_repository or SqliteSessionRepository()
        self._transcript_service = TranscriptService(
            self.session_repository,
            self._event_pipeline,
        )
        configured_transcribers = (
            default_transcribers() if transcribers is None else transcribers
        )
        self._transcribers = configured_transcribers
        self._transcribed_event_types = self._transcribers.event_types
        self._detail_level = EventDetailLevel.NORMAL
        self._sidebar_statistics = sidebar_statistics
        self._subscriptions: list[Subscription[BaseModel]] = []
        if self._sidebar_statistics is not None:
            self._sidebar_statistics.setup(self._event_pipeline, agents_by_id)
        self._controller = AgentCommandController(
            agents_by_id,
            event_pipeline,
            self.session_repository,
        )
        self._pane_ids = {
            agent.configuration().id: f"agent-chat-{index}"
            for index, agent in enumerate(agents_by_id.values())
        }
        self.chats = {
            agent.configuration().id: AgentChatPane(
                agent,
                agents_by_id,
                self.session_repository,
                self._event_pipeline,
                self._transcript_service,
                self._transcribers,
                self._detail_level,
                sidebar_statistics=(
                    self._sidebar_statistics
                    or default_sidebar_statistics(agent)
                ),
                id=f"chat-content-{index}",
            )
            for index, agent in enumerate(agents_by_id.values())
        }

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial=next(iter(self._pane_ids.values())), id="agent-tabs"):
            for agent_id, pane_id in self._pane_ids.items():
                with TabPane(self._agents[agent_id].configuration().name, id=pane_id):
                    yield self.chats[agent_id]
        yield Footer()

    async def on_mount(self) -> None:
        self._subscribe(TranscriptEntryAdded, self._receive_transcript_entry_added)
        self._subscribe(TranscriptEntryUpdated, self._receive_transcript_entry_updated)
        self._subscribe(TranscriptEntryDeleted, self._receive_transcript_entry_deleted)
        for event_type in self._transcribed_event_types:
            self._subscribe(event_type, self._transcribe_event)
        self._subscribe(SessionCreated, self._receive_session_created)
        self._subscribe(SessionDeleted, self._receive_session_deleted)
        self._active_chat().input.focus()
        self.set_interval(0.25, self._refresh_sidebars)

    async def on_unmount(self) -> None:
        self._stop_event_delivery()
        if self._owns_repository:
            await self.session_repository.close()

    async def action_quit(self) -> None:
        """Release UI delivery tasks before asking Textual to leave its message loop."""
        self._stop_event_delivery()
        self.exit()

    async def _transcribe_event(
        self,
        event: BaseModel,
        _subscription: Subscription[BaseModel],
    ) -> None:
        transcriber = self._transcribers.event_transcriber_for(event)
        routed = transcriber.transcribe(event)
        if routed.agent_id in self.chats:
            await self._transcript_service.add(
                routed.agent_id,
                routed.session_name,
                routed.entry,
            )

    async def _receive_session_created(
        self,
        event: SessionCreated,
        _subscription: Subscription[SessionCreated],
    ) -> None:
        agent = self._agents.get(event.agent_id)
        if agent is None:
            return

        # Provider-driven session creation bypasses the command controller, so
        # the event consumer must establish persistence before updating the UI.
        await self.session_repository.save_session(
            event.agent_id,
            event.session_name,
            event.identifier,
        )
        active = agent.current_session()
        if active is not None and active.name == event.session_name:
            pane = self.chats.get(event.agent_id)
            if pane is not None:
                await pane.select_session(event.session_name)

    async def _receive_session_deleted(
        self,
        event: SessionDeleted,
        _subscription: Subscription[SessionDeleted],
    ) -> None:
        pane = self.chats.get(event.agent_id)
        if pane is not None:
            await pane.select_session(event.selected_session_name)

    async def _receive_transcript_entry_added(
        self,
        event: TranscriptEntryAdded,
        _subscription: Subscription[TranscriptEntryAdded],
    ) -> None:
        pane = self.chats.get(event.agent_id)
        if pane is not None:
            try:
                await pane.add_entry(event.session_name, event.entry)
            except NoMatches:
                # Event delivery can race with application teardown.
                return

    async def _receive_transcript_entry_updated(
        self,
        event: TranscriptEntryUpdated,
        _subscription: Subscription[TranscriptEntryUpdated],
    ) -> None:
        pane = self.chats.get(event.agent_id)
        if pane is not None:
            try:
                await pane.update_entry(event.session_name, event.entry)
            except NoMatches:
                return

    async def _receive_transcript_entry_deleted(
        self,
        event: TranscriptEntryDeleted,
        _subscription: Subscription[TranscriptEntryDeleted],
    ) -> None:
        pane = self.chats.get(event.agent_id)
        if pane is not None:
            try:
                await pane.delete_entry(event.session_name, event.entry_id)
            except NoMatches:
                return

    @on(ChatEditor.Submitted)
    async def submit_chat_input(self, message: ChatEditor.Submitted) -> None:
        pane = next(
            (candidate for candidate in self.chats.values() if candidate.input is message.editor),
            None,
        )
        if pane is None:
            return
        try:
            outcome = await self._controller.execute(pane.agent_id, message.value)
        except (KeyError, RuntimeError, ValueError) as error:
            await self._append_to_current(
                pane,
                TranscriptEntry[SystemTranscriptMetadata](
                    "system", f"Error: {error}", EventDetailLevel.NORMAL, {"type": "system"}
                ),
            )
            return
        message.editor.clear()
        if outcome.session_name is not None and outcome.session_name != pane.session_name:
            await pane.select_session(outcome.session_name)
        if outcome.entry is not None:
            await self._append_to_current(pane, outcome.entry)
        if outcome.selected_agent is not None:
            self.select_agent(outcome.selected_agent)

    @on(AgentChatPane.DetailLevelChanged)
    async def change_detail_level(self, message: AgentChatPane.DetailLevelChanged) -> None:
        self._detail_level = message.detail_level
        for pane in self.chats.values():
            await pane.set_detail_level(message.detail_level)

    def select_agent(self, agent_id: UUID) -> None:
        tabs = self.query_one("#agent-tabs", TabbedContent)
        tabs.active = self._pane_ids[agent_id]
        self.chats[agent_id].input.focus()

    def _active_chat(self) -> AgentChatPane:
        tabs = self.query_one("#agent-tabs", TabbedContent)
        agent_id = next(
            configured_id
            for configured_id, pane_id in self._pane_ids.items()
            if pane_id == tabs.active
        )
        return self.chats[agent_id]

    async def _append_to_current(
        self,
        pane: AgentChatPane,
        entry: TranscriptEntry[TranscriptMetadata],
    ) -> None:
        if pane.session_name is None:
            raise RuntimeError("The selected agent has no active session.")
        await self._transcript_service.add(
            pane.agent_id,
            pane.session_name,
            entry,
        )

    def _subscribe(
        self,
        event_type: type[AppEventT],
        callback: EventCallback[AppEventT],
    ) -> None:
        subscription = self._event_pipeline.subscribe(event_type, callback)
        self._subscriptions.append(
            cast(Subscription[BaseModel], subscription)
        )

    def _stop_event_delivery(self) -> None:
        """Detach this UI's subscriptions without taking ownership of the pipeline."""
        for subscription in self._subscriptions:
            self._event_pipeline.unsubscribe(subscription)
        self._subscriptions.clear()

    async def _refresh_sidebars(self) -> None:
        for pane in self.chats.values():
            try:
                await pane.refresh_sidebar()
            except NoMatches:
                # A timer tick can race with Textual unmounting a chat pane.
                continue


class UserInterfaceLauncher:
    """Callable entry point for the default Textual interface."""

    async def __call__(
        self,
        agents: Mapping[UUID, Agent[SessionIdentifier]],
        event_pipeline: EventPipeline,
        session_repository: SessionRepository | None = None,
        *,
        transcribers: TranscriberRegistry | None = None,
        sidebar_statistics: SidebarStatistics | None = None,
    ) -> None:
        await AgentChatApp(
            agents,
            event_pipeline,
            session_repository,
            transcribers=transcribers,
            sidebar_statistics=sidebar_statistics,
        ).run_async()


run_ui = UserInterfaceLauncher()
