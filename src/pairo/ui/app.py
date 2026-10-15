"""Textual application for operating asynchronous provider-neutral agents."""

from __future__ import annotations

import asyncio
from uuid import UUID

from pydantic import BaseModel
from textual import on
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, Input, TabbedContent, TabPane

from pairo.core.agent import Runner
from pairo.core.events import (
    AgentTokenUsageUpdated,
    PermissionRequest,
    PermissionResponse,
    SessionCreated,
    SessionDeleted,
)

from .commands import AgentCommandController
from .models import (
    EventDetailLevel,
    SystemTranscriptMetadata,
    TranscriptEntry,
)
from .session_repository import SessionRepository
from .sqlite_session_repository import SqliteSessionRepository
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
    AgentSidebar,
)
from .widgets.sidebar_statistics import SidebarStatistics, default_sidebar_statistics


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
    .sidebar-information-scroll, .session-list-scroll { height: 1fr; min-height: 3; }
    .sidebar-information-picker { width: 1fr; height: 3; margin-bottom: 1; }
    .sidebar-field {
        height: auto; border: round $primary-background; background: $surface;
    }
    .sidebar-field-label { height: 1; text-style: bold; color: $accent; }
    .sidebar-field-value { height: auto; }
    .detail-filter { width: 14; min-width: 14; height: 3; margin-left: 1; }
    .session-switch-note { height: auto; color: $text-muted; }
    .session-list { height: auto; }
    .session-row { width: 1fr; height: auto; min-height: 3; margin-top: 1; }
    .session-button { width: 1fr; height: auto; min-height: 3; }
    .session-delete-button { width: 5; min-width: 5; height: 3; }
    .current-session { border: heavy $accent; }
    .chat-input-container { layout: horizontal; height: 3; margin: 1 0; }
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
    .permission-actions { height: 3; }
    .permission-actions Button { width: 1fr; min-width: 16; }
    .permission-decision { height: auto; color: $text-muted; text-style: bold; }
    .chat-input { width: 1fr; height: 3; }
    """
    BINDINGS = [("ctrl+q", "quit", "Quit")]


    def __init__(
        self,
        runner: Runner,
        session_repository: SessionRepository | None = None,
        *,
        transcribers: TranscriberRegistry | None = None,
        sidebar_statistics: SidebarStatistics | None = None,
    ) -> None:
        super().__init__()
        agents_by_id = dict(runner.agents)
        self._runner = runner
        self._agents = agents_by_id
        self._event_pipeline = runner.pipeline
        self._owns_repository = session_repository is None
        self.session_repository = session_repository or SqliteSessionRepository()
        self.history = self.session_repository
        configured_transcribers = (
            default_transcribers() if transcribers is None else transcribers
        )
        self._transcribers = configured_transcribers
        self._transcribed_event_types = self._transcribers.event_types
        self._token_usage_by_session: dict[
            tuple[UUID, str], AgentTokenUsageUpdated
        ] = {}
        self._detail_level = EventDetailLevel.NORMAL
        self._sidebar_statistics = sidebar_statistics
        self._controller = AgentCommandController(
            runner,
            self.session_repository,
        )
        self._pane_ids = {
            agent.id: f"agent-chat-{index}"
            for index, agent in enumerate(agents_by_id.values())
        }
        self.chats = {
            agent.id: AgentChatPane(
                agent,
                runner,
                self.session_repository,
                self._event_pipeline,
                self._transcribers,
                self._detail_level,
                sidebar_statistics=(
                    self._sidebar_statistics
                    or default_sidebar_statistics(agent, self._token_usage_for_session)
                ),
                id=f"chat-content-{index}",
            )
            for index, agent in enumerate(agents_by_id.values())
        }

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial=next(iter(self._pane_ids.values())), id="agent-tabs"):
            for agent_id, pane_id in self._pane_ids.items():
                with TabPane(self._agents[agent_id].name, id=pane_id):
                    yield self.chats[agent_id]
        yield Footer()

    async def on_mount(self) -> None:
        replay_tasks: list[asyncio.Task[None]] = []
        self._event_pipeline.add_listener(TranscriptEntryAdded, self._persist_transcript_entry_added)
        self._event_pipeline.add_listener(TranscriptEntryUpdated, self._persist_transcript_entry_updated)
        self._event_pipeline.add_listener(TranscriptEntryDeleted, self._persist_transcript_entry_deleted)
        token_usage_replay = self._event_pipeline.add_listener(
            AgentTokenUsageUpdated,
            self._track_token_usage,
            process_existing_events=True,
        )
        if token_usage_replay is not None:
            replay_tasks.append(token_usage_replay)
        for event_type in self._transcribed_event_types:
            replay = self._event_pipeline.add_listener(
                event_type,
                self._transcribe_event,
                process_existing_events=True,
            )
            if replay is not None:
                replay_tasks.append(replay)
        created_replay = self._event_pipeline.add_listener(
            SessionCreated,
            self._receive_session_created,
            process_existing_events=True,
        )
        if created_replay is not None:
            replay_tasks.append(created_replay)
        deleted_replay = self._event_pipeline.add_listener(
            SessionDeleted,
            self._receive_session_deleted,
            process_existing_events=True,
        )
        if deleted_replay is not None:
            replay_tasks.append(deleted_replay)
        self._answered_request_ids = {
            event.request_id
            for event in self._event_pipeline.events
            if isinstance(event, PermissionResponse)
        }
        await self._controller.wait_until_ready()
        if replay_tasks:
            await asyncio.gather(*replay_tasks)
        self._active_chat().input.focus()
        self.set_interval(0.25, self._refresh_sidebars)

    async def on_unmount(self) -> None:
        self._event_pipeline.remove_listener(
            TranscriptEntryAdded, self._persist_transcript_entry_added
        )
        self._event_pipeline.remove_listener(
            TranscriptEntryUpdated, self._persist_transcript_entry_updated
        )
        self._event_pipeline.remove_listener(
            TranscriptEntryDeleted, self._persist_transcript_entry_deleted
        )
        self._event_pipeline.remove_listener(
            AgentTokenUsageUpdated, self._track_token_usage
        )
        for event_type in self._transcribed_event_types:
            self._event_pipeline.remove_listener(event_type, self._transcribe_event)
        self._event_pipeline.remove_listener(SessionCreated, self._receive_session_created)
        self._event_pipeline.remove_listener(SessionDeleted, self._receive_session_deleted)
        if self._owns_repository:
            await self.session_repository.close()

    def _transcribe_event(self, event: BaseModel) -> BaseModel:
        if (
            isinstance(event, PermissionRequest)
            and event.request_id in self._answered_request_ids
        ):
            return event
        transcriber = self._transcribers.event_transcriber_for(event)
        routed = transcriber.transcribe(event)
        if routed.agent_id in self.chats:
            self._event_pipeline.enqueue(
                TranscriptEntryAdded(
                    agent_id=routed.agent_id,
                    session_name=routed.session_name,
                    entry=routed.entry,
                )
            )
        return event

    def _track_token_usage(
        self,
        event: AgentTokenUsageUpdated,
    ) -> AgentTokenUsageUpdated:
        self._token_usage_by_session[(event.agent_id, event.session_name)] = event
        return event

    def _token_usage_for_session(
        self, agent_id: UUID, session_name: str
    ) -> AgentTokenUsageUpdated | None:
        return self._token_usage_by_session.get((agent_id, session_name))

    @property
    def active_token_usage(self) -> AgentTokenUsageUpdated | None:
        """Latest usage for the agent and session currently shown in the UI."""
        pane = self._active_chat()
        if pane.session_name is None:
            return None
        return self._token_usage_by_session.get((pane.agent_id, pane.session_name))

    def _receive_session_created(self, event: SessionCreated) -> SessionCreated:
        if event.session_name in self._runner.session_names(event.agent_id):
            self.call_later(self._apply_session_created, event)
        return event

    async def _apply_session_created(self, event: SessionCreated) -> None:
        pane = self.chats.get(event.agent_id)
        if pane is not None:
            await pane.select_session(event.session_name)

    def _receive_session_deleted(self, event: SessionDeleted) -> SessionDeleted:
        self._token_usage_by_session.pop((event.agent_id, event.session_name), None)
        self.call_later(self._apply_session_deleted, event)
        return event

    async def _apply_session_deleted(self, event: SessionDeleted) -> None:
        pane = self.chats.get(event.agent_id)
        if pane is not None:
            await pane.select_session(event.selected_session_name)

    async def _persist_transcript_entry_added(
        self, event: TranscriptEntryAdded
    ) -> TranscriptEntryAdded:
        await self.session_repository.add_entry(
            event.agent_id, event.session_name, event.entry
        )
        self.call_later(self._apply_transcript_entry_added, event)
        return event

    async def _apply_transcript_entry_added(self, event: TranscriptEntryAdded) -> None:
        pane = self.chats.get(event.agent_id)
        if pane is not None:
            try:
                await pane.add_entry(event.session_name, event.entry)
            except NoMatches:
                # A deferred application can race with application teardown.
                return

    async def _persist_transcript_entry_updated(
        self, event: TranscriptEntryUpdated
    ) -> TranscriptEntryUpdated:
        await self.session_repository.update_entry(
            event.agent_id, event.session_name, event.entry
        )
        self.call_later(self._apply_transcript_entry_updated, event)
        return event

    async def _apply_transcript_entry_updated(self, event: TranscriptEntryUpdated) -> None:
        pane = self.chats.get(event.agent_id)
        if pane is not None:
            try:
                await pane.update_entry(event.session_name, event.entry)
            except NoMatches:
                return

    async def _persist_transcript_entry_deleted(
        self, event: TranscriptEntryDeleted
    ) -> TranscriptEntryDeleted:
        await self.session_repository.delete_entry(
            event.agent_id, event.session_name, event.entry_id
        )
        self.call_later(self._apply_transcript_entry_deleted, event)
        return event

    async def _apply_transcript_entry_deleted(self, event: TranscriptEntryDeleted) -> None:
        pane = self.chats.get(event.agent_id)
        if pane is not None:
            try:
                await pane.delete_entry(event.session_name, event.entry_id)
            except NoMatches:
                return

    @on(Input.Submitted)
    async def submit_chat_input(self, message: Input.Submitted) -> None:
        pane = next(
            (candidate for candidate in self.chats.values() if candidate.input is message.input),
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
        message.input.clear()
        if outcome.session_name is not None and outcome.session_name != pane.session_name:
            await pane.select_session(outcome.session_name)
        if outcome.entry is not None:
            await self._append_to_current(pane, outcome.entry)
        if outcome.selected_agent is not None:
            self.select_agent(outcome.selected_agent)

    @on(AgentSidebar.SessionSelected)
    async def select_sidebar_session(self, message: AgentSidebar.SessionSelected) -> None:
        pane = self.chats[message.agent_id]
        try:
            selected = await self._controller.select_session(
                message.agent_id,
                message.session_name,
            )
            await pane.select_session(selected)
            await self._append_to_current(
                pane,
                TranscriptEntry[SystemTranscriptMetadata](
                    "system",
                    f"Switched to session {selected}.",
                    EventDetailLevel.NORMAL,
                    {"type": "system"},
                ),
            )
        except (KeyError, RuntimeError, ValueError) as error:
            await self._append_to_current(
                pane,
                TranscriptEntry[SystemTranscriptMetadata](
                    "system",
                    f"Unable to switch session: {error}",
                    EventDetailLevel.NORMAL,
                    {"type": "system"},
                ),
            )

    @on(AgentSidebar.SessionDeletionRequested)
    async def delete_sidebar_session(
        self,
        message: AgentSidebar.SessionDeletionRequested,
    ) -> None:
        pane = self.chats[message.agent_id]
        try:
            deleted, selected = await self._controller.delete_session(
                message.agent_id,
                message.session_name,
            )
            await pane.select_session(selected)
            await self._append_to_current(
                pane,
                TranscriptEntry[SystemTranscriptMetadata](
                    "system",
                    f"Deleted session {deleted}. Current session: {selected}.",
                    EventDetailLevel.NORMAL,
                    {"type": "system"},
                ),
            )
        except (KeyError, RuntimeError, ValueError) as error:
            await self._append_to_current(
                pane,
                TranscriptEntry[SystemTranscriptMetadata](
                    "system",
                    f"Unable to delete session: {error}",
                    EventDetailLevel.NORMAL,
                    {"type": "system"},
                ),
            )
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
        entry: TranscriptEntry,
    ) -> None:
        if pane.session_name is None:
            raise RuntimeError("The selected agent has no active session.")
        await self._event_pipeline.enqueue(
            TranscriptEntryAdded(
                agent_id=pane.agent_id,
                session_name=pane.session_name,
                entry=entry,
            )
        )

    async def _refresh_sidebars(self) -> None:
        for pane in self.chats.values():
            try:
                await pane.refresh_sidebar()
            except NoMatches:
                # A timer tick can race with Textual unmounting a chat pane.
                continue


async def run_ui(
    runner: Runner,
    session_repository: SessionRepository | None = None,
    *,
    transcribers: TranscriberRegistry | None = None,
    sidebar_statistics: SidebarStatistics | None = None,
) -> None:
    await AgentChatApp(
        runner,
        session_repository,
        transcribers=transcribers,
        sidebar_statistics=sidebar_statistics,
    ).run_async()
    
