"""Textual application for operating asynchronous provider-neutral agents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from pydantic import BaseModel
from textual import on
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, TabbedContent, TabPane

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import (
    AgentError,
    AgentResponseCompleted,
    AgentResponseInterrupted,
    AgentTokenUsageUpdated,
    EventPipeline,
    MessageSent,
    SessionCompacted,
    SessionCreated,
    SessionDeleted,
    SessionUpdated,
)

from .pairo_event_piper import PairoEvent, TextualEventPiper, TextualIngressPolicy, on_pairo
from .chat_commands import (
    AgentSelectionCommand,
    CompactCommand,
    DeleteCommand,
    HelpCommand,
    ImageCommand,
    InterruptCommand,
    SessionCommand,
    SessionsCommand,
    SlashCommand,
)
from .commands import AgentCommandController
from .configuration import UserInterfaceConfiguration
from .models import (
    EventDetailLevel,
    SystemTranscriptMetadata,
    TranscriptEntry,
    TranscriptMetadata,
)
from .navigation_events import AgentSwitchRequested, SessionSwitchRequested
from .session_repository import SessionNotFoundError, SessionRepository
from .sqlite_session_repository import SqliteSessionRepository
from .transcript_events import (
    AddTranscriptEntry,
    UpdateTranscriptEntry,
    DeleteTranscriptEntry,
)
from .transcribers import (
    RoutedTranscriptEntry,
    TranscriberRegistry,
    default_transcribers,
)
from .widgets import (
    AgentChatPane,
)
from .widgets.chat_editor import ChatEditor
from .widgets.sidebar_statistics import (
    SidebarStatistic,
    UpdateAgentStatistic,
    UpdateSessionStatistic,
    create_default_sidebar_statistics,
)


class AgentChatApp(App[None]):
    TITLE = "pairo-loops"
    SUB_TITLE = "Multi-agent console"
    CSS = """
    Screen { layout: vertical; }
    #agent-tabs, AgentChatPane, .agent-workspace { height: 1fr; }
    .chat-main { width: 1fr; height: 1fr; }
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
    .sourced-transcript { height: auto; margin: 1 0; border: round $primary-background; padding: 0 1; }
    .transcript-source { height: 1; text-style: bold; color: $accent; }
    .transcript-message { height: auto; max-height: 12; }
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
        session_repository: SessionRepository,
        *,
        transcribers: TranscriberRegistry,
        sidebar_statistics: Sequence[SidebarStatistic],
        commands: Sequence[SlashCommand],
    ) -> None:
        super().__init__()
        configured_agents = tuple(agents.values())
        configured_ids = [agent.configuration().id for agent in configured_agents]
        duplicate_ids = {
            agent_id for agent_id in configured_ids if configured_ids.count(agent_id) > 1
        }
        if duplicate_ids:
            duplicates = ", ".join(sorted(str(agent_id) for agent_id in duplicate_ids))
            raise ValueError(f"Agent IDs must be unique; duplicated: {duplicates}.")
        configured_names = [agent.configuration().name for agent in configured_agents]
        duplicate_names = {
            name for name in configured_names if configured_names.count(name) > 1
        }
        if duplicate_names:
            duplicates = ", ".join(sorted(duplicate_names))
            raise ValueError(f"Agent names must be unique; duplicated: {duplicates}.")
        agents_by_id = dict(agents)
        self._sidebar_statistics = tuple(sidebar_statistics)
        self._agents = agents_by_id
        self._event_pipeline = event_pipeline
        self.session_repository = session_repository
        self._transcribers = transcribers
        self._detail_level = EventDetailLevel.NORMAL
        self._statistic_modules = self._sidebar_statistics
        self._piper = TextualEventPiper(
            event_pipeline,
            self.post_message,
            TextualIngressPolicy(
                (
                    SessionCreated,
                    SessionUpdated,
                    SessionCompacted,
                    SessionDeleted,
                    AgentError,
                    AgentResponseCompleted,
                    AgentResponseInterrupted,
                    AgentTokenUsageUpdated,
                    MessageSent,
                    AddTranscriptEntry,
                    UpdateTranscriptEntry,
                    DeleteTranscriptEntry,
                    AgentSwitchRequested,
                    SessionSwitchRequested,
                    *self._transcribers.event_types,
                )
            ),
        )
        self._subscriptions: list[object] = []
        self._statistics_ready = False
        self._controller = AgentCommandController(
            agents_by_id,
            event_pipeline,
            self.session_repository,
            commands,
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
                self._transcribers,
                self._detail_level,
                sidebar_statistics=self._sidebar_statistics,
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
        """Attach the application-owned ingress once the Textual loop exists."""
        await self._configure_statistics()
        await self._load_repository_sessions()
        for chat in self.chats.values():
            await chat.sidebar.load(chat.session_name)

        self._statistics_ready = True
        self._piper.attach()
        self._subscriptions = [self._piper]
        self._active_chat().input.focus()

    async def _load_repository_sessions(self) -> None:
        """Restore each agent's selected session and transcript from durable state."""
        for agent_id, agent in self._agents.items():
            active = agent.current_session()
            if active is None:
                sessions = await self.session_repository.sessions_for(agent_id)
                if not sessions:
                    continue
                selected = sessions[-1]
                active = await agent.activate_session(
                    selected.session_name, selected.identifier
                )
            else:
                await self.session_repository.save_session(
                    agent_id,
                    active.name,
                    active.identifier.model_dump(mode="json"),
                )
            await self.chats[agent_id].select_session(active.name)

    async def on_unmount(self) -> None:
        self._stop_event_delivery()
        for statistic in self._statistic_modules:
            await statistic.teardown()
        await self._delete_unused_sessions()

    async def _configure_statistics(self) -> None:
        for statistic in self._statistic_modules:
            await statistic.configure(self.session_repository, self, self._agents.values())

    async def action_quit(self) -> None:
        self._stop_event_delivery()
        self.exit()

    @on_pairo(BaseModel)
    async def receive_pairo_event(self, event: BaseModel) -> None:
        """Deliver statistics and persist any converted transcript entry."""
        if self._transcribers.has_event_transcriber(event):
            routed = self._transcribers.event_transcriber_for(event).transcribe(event)
            await self._add_routed_entry(routed)
        for statistic in self._statistic_modules:
            statistic.post_message(PairoEvent(event))


    @on(UpdateAgentStatistic)
    def route_agent_statistic(self, message: UpdateAgentStatistic) -> None:
        pane = self.chats.get(message.agent_id)
        if pane is not None:
            try:
                pane.sidebar.update_agent(message)
            except NoMatches:
                return

    @on(UpdateSessionStatistic)
    def route_session_statistic(self, message: UpdateSessionStatistic) -> None:
        pane = self.chats.get(message.agent_id)
        if pane is not None:
            try:
                pane.sidebar.update_session(message)
            except NoMatches:
                return

    @on_pairo(SessionCreated)
    async def receive_session_created(self, event: SessionCreated) -> None:
        agent = self._agents.get(event.agent_id)
        if agent is None:
            return
        await self.session_repository.save_session(
            event.agent_id,
            event.session_name,
            event.identifier,
        )
        active = agent.current_session()
        if active is not None and active.name == event.session_name:
            pane = self.chats.get(event.agent_id)
            if pane is not None:
                try:
                    await pane.select_session(event.session_name)

                except NoMatches:
                    return

    @on_pairo(SessionDeleted)
    async def receive_session_deleted(self, event: SessionDeleted) -> None:
        pane = self.chats.get(event.agent_id)
        if pane is not None:
            try:
                await pane.select_session(event.selected_session_name)
            except (NoMatches):
                # Deletion events can arrive after teardown or a later deletion.
                return

    @on_pairo(AddTranscriptEntry)
    async def _add_transcript_request(self, message: AddTranscriptEntry) -> None:
        await self._add_routed_entry(
            RoutedTranscriptEntry(message.agent_id, message.session_name, message.entry)
        )

    @on_pairo(UpdateTranscriptEntry)
    async def _update_transcript_request(self, message: UpdateTranscriptEntry) -> None:
        try:
            await self.session_repository.update_entry(message.agent_id, message.session_name, message.entry)
        except (LookupError, RuntimeError, ValueError):
            self.log.error("Unable to update transcript entry")
            return
        pane = self.chats.get(message.agent_id)
        if pane is not None:
            try:
                await pane.update_entry(message.session_name, message.entry)
            except NoMatches:
                return

    @on_pairo(DeleteTranscriptEntry)
    async def _delete_transcript_request(self, message: DeleteTranscriptEntry) -> None:
        try:
            await self.session_repository.delete_entry(message.agent_id, message.session_name, message.entry_id)
        except (LookupError, RuntimeError, ValueError):
            self.log.error("Unable to delete transcript entry")
            return
        pane = self.chats.get(message.agent_id)
        if pane is not None:
            try:
                await pane.delete_entry(message.session_name, message.entry_id)
            except NoMatches:
                return

    @on_pairo(AgentSwitchRequested)
    async def receive_agent_switch_requested(
        self,
        event: AgentSwitchRequested,
    ) -> None:
        if event.agent_id not in self.chats:
            return
        try:
            self.select_agent(event.agent_id)
        except NoMatches:
            # Event delivery can race with application teardown.
            return

    @on_pairo(SessionSwitchRequested)
    async def receive_session_switch_requested(
        self,
        event: SessionSwitchRequested,
    ) -> None:
        pane = self.chats.get(event.agent_id)
        if pane is not None:
            try:
                await pane.select_session(event.session_name)
            except (NoMatches):
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
            await self._controller.execute(pane.agent_id, message.value)
        except (KeyError, RuntimeError, ValueError) as error:
            await self._append_to_current(
                pane,
                TranscriptEntry[SystemTranscriptMetadata](
                    "system", f"Error: {error}", EventDetailLevel.NORMAL, {"type": "system"}
                ),
            )
            return
        message.editor.clear()

    @on(AgentChatPane.DetailLevelChanged)
    async def change_detail_level(self, message: AgentChatPane.DetailLevelChanged) -> None:
        self._detail_level = message.detail_level
        for pane in self.chats.values():
            await pane.set_detail_level(message.detail_level)

    @on(TabbedContent.TabActivated)
    async def rebuild_statistics_for_active_tab(self, _event: TabbedContent.TabActivated) -> None:
        if not self._statistics_ready:
            return
        tabs = self.query_one("#agent-tabs", TabbedContent)
        agent_id = next((configured_id for configured_id, pane_id in self._pane_ids.items() if pane_id == tabs.active), None)
        if agent_id is not None:
            self.call_after_refresh(self.chats[agent_id].sidebar.load, self.chats[agent_id].session_name)

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
        await self._add_routed_entry(RoutedTranscriptEntry(pane.agent_id, pane.session_name, entry))

    async def _add_routed_entry(self, routed: RoutedTranscriptEntry[TranscriptMetadata]) -> bool:
        try:
            await self.session_repository.add_entry(routed.agent_id, routed.session_name, routed.entry)
        except SessionNotFoundError:
            return False
        pane = self.chats.get(routed.agent_id)
        if pane is not None:
            try:
                await pane.add_entry(routed.session_name, routed.entry)
            except NoMatches:
                pass
        return True

    def _stop_event_delivery(self) -> None:
        """Detach before teardown so no raw event can enqueue afterwards."""
        self._piper.detach()
        self._subscriptions.clear()

    async def _delete_unused_sessions(self):
        for agent in self._agents.values():
            sessions = await self.session_repository.sessions_for(agent.configuration().id)
            for session in sessions:
                session_data = await self.session_repository.get_session(
                    agent.configuration().id, session.session_name)
                if session_data.last_message_at is None:
                    await self.session_repository.delete_session(
                        agent.configuration().id, session.session_name
                )

class UserInterfaceLauncher:
    """Callable entry point for the default Textual interface."""

    async def __call__(
        self,
        agents: Mapping[UUID, Agent[SessionIdentifier]],
        event_pipeline: EventPipeline,
        configuration: UserInterfaceConfiguration | SessionRepository | None = None,
    ) -> None:
        configured = self._configuration(configuration)
        repository = configured.session_repository
        owns_repository = repository is None
        if repository is None:
            repository = SqliteSessionRepository()
        try:
            await AgentChatApp(
                agents,
                event_pipeline,
                repository,
                transcribers=(configured.transcribers or default_transcribers()),
                sidebar_statistics=self._sidebar_statistics(configured),
                commands=self._commands(configured),
            ).run_async()
        finally:
            if owns_repository:
                await repository.close()

    def _configuration(
        self,
        configuration: UserInterfaceConfiguration | SessionRepository | None,
    ) -> UserInterfaceConfiguration:
        if isinstance(configuration, UserInterfaceConfiguration):
            return configuration
        return UserInterfaceConfiguration(configuration)

    def _commands(
        self, configuration: UserInterfaceConfiguration
    ) -> tuple[SlashCommand, ...]:
        defaults: tuple[SlashCommand, ...] = ()
        if configuration.include_default_commands:
            defaults = (
                HelpCommand(), AgentSelectionCommand(), SessionCommand(),
                SessionsCommand(), CompactCommand(), DeleteCommand(),
                InterruptCommand(), ImageCommand(),
            )
        return defaults + tuple(configuration.commands)

    def _sidebar_statistics(
        self, configuration: UserInterfaceConfiguration
    ) -> tuple[SidebarStatistic, ...]:
        if configuration.sidebar_statistics is not None:
            return configuration.sidebar_statistics
        return create_default_sidebar_statistics()
    


run_ui = UserInterfaceLauncher()
