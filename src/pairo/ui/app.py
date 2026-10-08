"""Textual application for operating asynchronous provider-neutral agents."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel
from textual import on
from textual.app import App, ComposeResult
from textual.message import Message
from textual.widgets import Footer, Header, Input, TabbedContent, TabPane

from pairo.core.events import (
    AgentError,
    AgentEvent,
    EventPipeline,
    MessageSent,
    NetworkMessageSent,
    PermissionRequest,
    PermissionResponse,
    SessionUpdated,
)

from .commands import AgentCommandController, ChatAgent
from .formatting import CompletedEventFormatter
from .models import (
    EventDetailLevel,
    TranscriptEntry,
)
from .permissions import build_permission_response
from .presentation import permission_request_history_content
from .session_repository import SessionRepository
from .sqlite_session_repository import SqliteSessionRepository
from .widgets import (
    AgentChatPane,
    AgentSidebar,
    PermissionRequestCard,
)


class PipelineEventReceived(Message):
    def __init__(self, event: BaseModel) -> None:
        super().__init__()
        self.event = event


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
    .session-button { width: 1fr; height: auto; min-height: 3; margin-top: 1; }
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

    _DISPLAYED_EVENT_TYPES = (
        AgentError,
        AgentEvent,
        MessageSent,
        NetworkMessageSent,
        PermissionRequest,
    )

    def __init__(
        self,
        agents: Sequence[ChatAgent],
        event_pipeline: EventPipeline,
        session_repository: SessionRepository | None = None,
    ) -> None:
        super().__init__()
        if not agents:
            raise ValueError("AgentChatApp requires at least one agent.")
        agents_by_name = {agent.name: agent for agent in agents}
        if len(agents_by_name) != len(agents):
            raise ValueError("Agent names must be unique.")
        self._agents = agents_by_name
        self._event_pipeline = event_pipeline
        self._owns_repository = session_repository is None
        self.session_repository = session_repository or SqliteSessionRepository()
        self.history = self.session_repository
        self._formatter = CompletedEventFormatter()
        self._detail_level = EventDetailLevel.NORMAL
        self._controller = AgentCommandController(
            agents_by_name,
            event_pipeline,
            self.session_repository,
        )
        self._pane_ids = {
            agent.name: f"agent-chat-{index}" for index, agent in enumerate(agents)
        }
        self.chats = {
            agent.name: AgentChatPane(
                agent,
                self.session_repository,
                self._detail_level,
                id=f"chat-content-{index}",
            )
            for index, agent in enumerate(agents)
        }

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial=next(iter(self._pane_ids.values())), id="agent-tabs"):
            for agent_name, pane_id in self._pane_ids.items():
                with TabPane(agent_name, id=pane_id):
                    yield self.chats[agent_name]
        yield Footer()

    async def on_mount(self) -> None:
        for event_type in self._DISPLAYED_EVENT_TYPES:
            self._event_pipeline.add_listener(event_type, self._receive_pipeline_event)
        replayed_events = tuple(self._event_pipeline.events)
        answered_request_ids = {
            event.request_id for event in replayed_events
            if isinstance(event, PermissionResponse)
        }
        for event in replayed_events:
            if isinstance(event, SessionUpdated):
                await self._save_session(event)
            elif isinstance(event, self._DISPLAYED_EVENT_TYPES):
                if (
                    isinstance(event, PermissionRequest)
                    and event.request_id in answered_request_ids
                ):
                    continue
                self._receive_pipeline_event(event)
        self._active_chat().input.focus()
        self.set_interval(0.25, self._refresh_sidebars)

    async def on_unmount(self) -> None:
        for event_type in self._DISPLAYED_EVENT_TYPES:
            self._event_pipeline.remove_listener(event_type, self._receive_pipeline_event)
        if self._owns_repository:
            await self.session_repository.close()

    def _receive_pipeline_event(self, event: BaseModel) -> BaseModel:
        self.post_message(PipelineEventReceived(event))
        return event

    @on(PipelineEventReceived)
    async def show_pipeline_event(self, message: PipelineEventReceived) -> None:
        event = message.event
        if isinstance(event, PermissionRequest):
            pane = self.chats.get(event.agent_name)
            if pane is not None:
                # Persist the request for history, but render the richer card only live.
                await pane.append(
                    event.session_name,
                    TranscriptEntry(
                        "permission",
                        permission_request_history_content(event),
                        EventDetailLevel.NORMAL,
                        {"type": "permission_request", "request_id": event.request_id},
                    ),
                    display=False,
                )
                await pane.enqueue_permission(event)
            return
        routed = self._formatter.format(event)
        if routed is not None and routed.agent_name in self.chats:
            await self.chats[routed.agent_name].append(
                routed.session_name,
                routed.entry,
            )

    @on(Input.Submitted)
    async def submit_chat_input(self, message: Input.Submitted) -> None:
        pane = next(
            (candidate for candidate in self.chats.values() if candidate.input is message.input),
            None,
        )
        if pane is None:
            return
        try:
            outcome = await self._controller.execute(pane.agent_name, message.value)
        except (KeyError, RuntimeError, ValueError) as error:
            await self._append_to_current(
                pane,
                TranscriptEntry(
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

    @on(PermissionRequestCard.Decided)
    async def answer_permission(self, message: PermissionRequestCard.Decided) -> None:
        request = message.request
        pane = self.chats.get(request.agent_name)
        if pane is None:
            return
        try:
            await self._event_pipeline.publish(
                build_permission_response(request, message.decision)
            )
        except (RuntimeError, ValueError) as error:
            pane.allow_permission_retry(request)
            await pane.append(
                request.session_name,
                TranscriptEntry(
                    "system",
                    f"Unable to answer permission {request.request_id}: {error}",
                    EventDetailLevel.NORMAL,
                    {"type": "system"},
                ),
            )
            return
        pane.resolve_permission(request, message.decision)
        await pane.append(
            request.session_name,
            TranscriptEntry(
                "permission",
                f"Approval answered: {message.decision}",
                EventDetailLevel.NORMAL,
                {
                    "type": "permission_response",
                    "request_id": request.request_id,
                    "decision": message.decision,
                },
            ),
        )

    @on(AgentSidebar.SessionSelected)
    async def select_sidebar_session(self, message: AgentSidebar.SessionSelected) -> None:
        pane = self.chats[message.agent_name]
        try:
            selected = await self._controller.select_session(
                message.agent_name,
                message.session_name,
            )
            await pane.select_session(selected)
            await self._append_to_current(
                pane,
                TranscriptEntry(
                    "system",
                    f"Switched to session {selected}.",
                    EventDetailLevel.NORMAL,
                    {"type": "system"},
                ),
            )
        except (KeyError, RuntimeError, ValueError) as error:
            await self._append_to_current(
                pane,
                TranscriptEntry(
                    "system",
                    f"Unable to switch session: {error}",
                    EventDetailLevel.NORMAL,
                    {"type": "system"},
                ),
            )

    @on(AgentChatPane.DetailLevelChanged)
    async def change_detail_level(self, message: AgentChatPane.DetailLevelChanged) -> None:
        self._detail_level = message.detail_level
        for pane in self.chats.values():
            await pane.set_detail_level(message.detail_level)

    def select_agent(self, agent_name: str) -> None:
        tabs = self.query_one("#agent-tabs", TabbedContent)
        tabs.active = self._pane_ids[agent_name]
        self.chats[agent_name].input.focus()

    def _active_chat(self) -> AgentChatPane:
        tabs = self.query_one("#agent-tabs", TabbedContent)
        agent_name = next(
            name for name, pane_id in self._pane_ids.items() if pane_id == tabs.active
        )
        return self.chats[agent_name]

    async def _append_to_current(
        self,
        pane: AgentChatPane,
        entry: TranscriptEntry,
    ) -> None:
        if pane.session_name is None:
            raise RuntimeError("The selected agent has no active session.")
        await pane.append(pane.session_name, entry)

    async def _save_session(self, event: SessionUpdated) -> None:
        await self.session_repository.save_session(
            event.agent_name,
            event.session_name,
            event.identifier,
        )

    async def _refresh_sidebars(self) -> None:
        for pane in self.chats.values():
            await pane.refresh_sidebar()


async def run_ui(
    agents: Sequence[ChatAgent],
    event_pipeline: EventPipeline,
    session_repository: SessionRepository | None = None,
) -> None:
    await AgentChatApp(agents, event_pipeline, session_repository).run_async()
    
