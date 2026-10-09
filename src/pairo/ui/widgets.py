"""Reusable Textual widgets for agent sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual import events
from textual.events import MouseScrollDown, MouseScrollUp
from textual.message import Message
from textual.widgets import Button, Input, Select, Static

from pairo.core.events import PermissionRequest

from .commands import ChatAgent
from .models import EventDetailLevel, SessionMetadata, TranscriptEntry
from .presentation import PermissionRequestPresenter
from .session_repository import SessionNotFoundError, SessionRepository




class Transcript(VerticalScroll):
    ROLE_STYLES = {
        "user": "bold cyan",
        "agent": "bold green",
        "activity": "bold yellow",
        "network": "bold magenta",
        "permission": "bold red",
        "system": "bold bright_black",
    }

    def __init__(self, detail_level: EventDetailLevel = EventDetailLevel.NORMAL, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.entries: list[TranscriptEntry] = []
        self.detail_level = detail_level
        self._render_generation = 0

    @property
    def visible_entries(self) -> tuple[TranscriptEntry, ...]:
        return tuple(entry for entry in self.entries if self._is_visible(entry))

    async def append_entry(self, entry: TranscriptEntry, *, display: bool = True) -> None:
        self.entries.append(entry)
        if display and self._is_visible(entry):
            await self._mount_entry(entry, scroll_after_mount=True)

    async def replace_entries(
        self,
        entries: Iterable[TranscriptEntry],
        *,
        inline_permission_cards: dict[int, "PermissionRequestCard"] | None = None,
    ) -> None:
        self._render_generation += 1
        self.entries = list(entries)
        await self.remove_children()
        cards = inline_permission_cards or {}
        for index, entry in enumerate(self.entries):
            if not self._is_visible(entry):
                continue
            card = cards.get(index)
            if card is None:
                await self._mount_entry(entry, scroll_after_mount=False)
            else:
                await self.mount(card)
        self._scroll_to_latest_after_layout()
        
    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        event.prevent_default()

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        event.prevent_default()


    async def set_detail_level(
        self,
        detail_level: EventDetailLevel,
        *,
        inline_permission_cards: dict[int, "PermissionRequestCard"] | None = None,
    ) -> None:
        if self.detail_level != detail_level:
            self.detail_level = detail_level
            await self.replace_entries(
                self.entries, inline_permission_cards=inline_permission_cards
            )

    async def _mount_entry(self, entry: TranscriptEntry, *, scroll_after_mount: bool) -> None:
        label = Text(f"{entry.role.upper()}  ", style=self.ROLE_STYLES.get(entry.role, "bold"))
        await self.mount(TranscriptMessage(Static(Text.assemble(label, entry.content), markup=False)))
        if scroll_after_mount:
            self._scroll_to_latest_after_layout()

    def _scroll_to_latest_after_layout(self) -> None:
        """Scroll after Textual has measured newly mounted content."""
        generation = self._render_generation
        self.call_after_refresh(
            lambda: self.scroll_end(animate=False)
            if generation == self._render_generation else None
        )

    def _is_visible(self, entry: TranscriptEntry) -> bool:
        return self.detail_level is EventDetailLevel.DEBUG or entry.detail_level is EventDetailLevel.NORMAL


class TranscriptMessage(VerticalScroll):
    """A capped ordinary transcript message that owns pointer-wheel scrolling."""

    def __init__(self, *children: object, **kwargs: object) -> None:
        super().__init__(*children, classes="transcript-message", **kwargs)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()


class PermissionRequestCard(Vertical):
    """A live, inline approval prompt. It is intentionally not persisted."""

    class Decided(Message):
        def __init__(self, request: PermissionRequest, decision: str) -> None:
            super().__init__()
            self.request = request
            self.decision = decision

    def __init__(self, request: PermissionRequest, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.request = request
        self._presenter = PermissionRequestPresenter()
        self._decision: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(Text(self._presenter.present(self.request)), classes="permission-details")
        with Horizontal(classes="permission-actions"):
            yield Button("Approve once", name="accept", variant="success")
            yield Button("Approve session", name="acceptForSession", variant="primary")
            yield Button("Decline", name="decline", variant="error")
        yield Static("", classes="permission-decision")

    def on_mount(self) -> None:
        label = self.query_one(".permission-decision", Static)
        if self._decision is None:
            label.display = False
        else:
            label.update(f"Decision: {self._decision_label(self._decision)}")
            label.display = True

    @on(Button.Pressed)
    def decide(self, event: Button.Pressed) -> None:
        decision = event.button.name
        if self._decision is None and decision is not None:
            event.stop()
            self._set_actions_disabled(True)
            self.post_message(self.Decided(self.request, decision))

    def resolve(self, decision: str) -> None:
        self._decision = decision
        self._set_actions_disabled(True)
        label = self.query_one(".permission-decision", Static)
        label.update(f"Decision: {self._decision_label(decision)}")
        label.display = True

    @property
    def is_pending(self) -> bool:
        return self._decision is None

    def allow_retry(self) -> None:
        if self._decision is None:
            self._set_actions_disabled(False)

    def _set_actions_disabled(self, disabled: bool) -> None:
        for button in self.query(Button):
            button.disabled = disabled

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()

    @staticmethod
    def _decision_label(decision: str) -> str:
        return {
            "accept": "Approved once",
            "acceptForSession": "Approved for session",
            "decline": "Denied",
        }.get(decision, decision)


class SidebarField(Vertical):
    def __init__(self, label: str, value: object) -> None:
        super().__init__(classes="sidebar-field")
        self._label, self._value = label, str(value)

    def compose(self) -> ComposeResult:
        yield Static(self._label, classes="sidebar-field-label")
        yield Static(self._value, classes="sidebar-field-value")


class SidebarFieldGroup(Vertical):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._fields: tuple[tuple[str, str], ...] = ()

    async def set_fields(self, fields: tuple[tuple[str, object], ...]) -> None:
        normalized = tuple((label, str(value)) for label, value in fields)
        if normalized != self._fields:
            self._fields = normalized
            await self.remove_children()
            await self.mount(*(SidebarField(label, value) for label, value in normalized))


class AgentSidebar(Vertical):
    _AGENT_INFORMATION = "agent"
    _SESSION_INFORMATION = "session"

    class SessionSelected(Message):
        def __init__(self, agent_name: str, session_name: str) -> None:
            super().__init__()
            self.agent_name = agent_name
            self.session_name = session_name

    def __init__(self, agent: ChatAgent, repository: SessionRepository, detail_level: EventDetailLevel, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.agent = agent
        self._repository = repository
        self._detail_level = detail_level
        self._session_snapshot: object | None = None
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
        yield Static("Sessions", classes="sidebar-heading")
        yield Static("", classes="session-switch-note")
        with VerticalScroll(classes="session-list-scroll"):
            yield Vertical(classes="session-list")

    async def on_mount(self) -> None:
        await self.refresh_data()

    @on(Select.Changed, ".sidebar-information-picker")
    async def change_information_mode(self, event: Select.Changed) -> None:
        if event.value is not Select.BLANK:
            self._information_mode = str(event.value)
            await self._refresh_information_fields()

    @on(Button.Pressed, ".session-button")
    def select_session(self, event: Button.Pressed) -> None:
        if event.button.name is not None:
            event.stop()
            self.post_message(self.SessionSelected(self.agent.name, event.button.name))

    async def refresh_data(self) -> None:
        await self._refresh_information_fields()
        generating = self.agent.is_generating
        self.query_one(".session-switch-note", Static).update(
            "Finish or interrupt the current response to switch sessions."
            if generating else "Select a session to resume it."
        )
        sessions = sorted(
            await self._repository.sessions_for(self.agent.name),
            key=lambda session: session.last_updated_at,
            reverse=True,
        )
        snapshot = (
            generating,
            self.agent.session_name,
            tuple((session.session_name, session.last_updated_at) for session in sessions),
        )
        if snapshot == self._session_snapshot:
            return
        self._session_snapshot = snapshot
        container = self.query_one(".session-list", Vertical)
        await container.remove_children()
        buttons = [
            Button(
                session.session_name,
                name=session.session_name,
                disabled=generating or session.session_name == self.agent.session_name,
                classes=(
                    "session-button current-session"
                    if session.session_name == self.agent.session_name
                    else "session-button"
                ),
            )
            for session in sessions
        ]
        await container.mount(
            *(buttons or [Static("No sessions yet.", classes="empty-session-list")])
        )

    async def _refresh_information_fields(self) -> None:
        fields = (
            self._agent_fields()
            if self._information_mode == self._AGENT_INFORMATION
            else await self._current_session_fields()
        )
        await self.query_one(".sidebar-information", SidebarFieldGroup).set_fields(fields)

    def _agent_fields(self) -> tuple[tuple[str, object], ...]:
        config = self.agent.configuration
        values = (
            ("Name", self.agent.name),
            ("State", getattr(self.agent.state, "value", self.agent.state)),
            ("Model", config.model),
            ("Reasoning", self._enum_value(config.reasoning_effort)),
            ("Sandbox", self._enum_value(config.sandbox)),
            ("Workspace", config.workspace),
            ("Approval mode", self._enum_value(config.approval_mode)),
            ("Web search", self._enum_value(config.web_search)),
            ("Prompt", config.base_instructions or "Not set"),
            ("Developer instructions", config.developer_instructions or "Not set"),
        )
        servers = self.agent.configured_mcp_servers
        return values + (
            tuple((f"MCP server: {server.name}", server.endpoint) for server in servers)
            if servers else (("MCP servers", "None configured"),)
        )

    async def _current_session_fields(self) -> tuple[tuple[str, object], ...]:
        session_name = self.agent.session_name
        if session_name is None:
            return (("Session", "No session selected."),)
        try:
            session = await self._repository.get_session(self.agent.name, session_name)
        except SessionNotFoundError:
            return (("Name", session_name),)
        return (
            ("Name", session.session_name),
            ("Created", self._format_time(session.created_at)),
            ("Last message", self._format_time(session.last_message_at)),
            ("Last update", self._format_time(session.last_updated_at)),
            ("Duration", self._format_duration(session.duration_seconds)),
        )

    @staticmethod
    def _enum_value(value: object | None) -> str:
        return "Default" if value is None else str(getattr(value, "value", value))

    @staticmethod
    def _format_time(value: datetime | None) -> str:
        if value is None:
            return "None"
        local = value.astimezone()
        offset = local.strftime("%z")
        readable_offset = f"{offset[:3]}:{offset[3:]}" if offset else ""
        return f"{local:%Y-%m-%d %H:%M:%S} {readable_offset}".rstrip()

    @staticmethod
    def _format_duration(seconds: float) -> str:
        whole_seconds = max(0, int(seconds))
        hours, remainder = divmod(whole_seconds, 3600)
        minutes, remaining_seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"


class AgentChatPane(Vertical):
    class DetailLevelChanged(Message):
        def __init__(self, detail_level: EventDetailLevel) -> None:
            super().__init__()
            self.detail_level = detail_level

    def __init__(self, agent: ChatAgent, repository: SessionRepository, detail_level: EventDetailLevel, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.agent = agent
        self.agent_name = agent.name
        self.session_name = agent.session_name
        self._repository = repository
        self._detail_level = detail_level
        self._permission_cards: dict[tuple[str, str], PermissionRequestCard] = {}
        self._pending_permission_positions: dict[tuple[str, str], int] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(classes="agent-workspace"):
            with Vertical(classes="chat-main"):
                yield Transcript(self._detail_level, classes="transcript")
                with Horizontal(classes="chat-input-container"):
                    yield Input(placeholder="Message this agent or enter /help", classes="chat-input")
                    yield Select(
                        [("Normal", "normal"), ("Debug", "debug")],
                        value=self._detail_level.value,
                        allow_blank=False,
                        classes="detail-filter",
                    )
            yield AgentSidebar(self.agent, self._repository, self._detail_level, classes="agent-sidebar")

    async def on_mount(self) -> None:
        if self.session_name is not None:
            try:
                entries = await self._repository.entries_for(self.agent_name, self.session_name)
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

    async def append(
        self, session_name: str, entry: TranscriptEntry, *, display: bool = True
    ) -> None:
        pending_key = self._pending_permission_key(session_name, entry)
        entry_position = None
        if pending_key is not None:
            existing_entries = await self._repository.entries_for(
                self.agent_name, session_name
            )
            entry_position = len(existing_entries)
        await self._repository.append_entry(self.agent_name, session_name, entry)
        if pending_key is not None and entry_position is not None:
            self._pending_permission_positions[pending_key] = entry_position
        if session_name == self.session_name:
            await self.transcript.append_entry(entry, display=display)
        await self.sidebar.refresh_data()

    async def select_session(self, session_name: str) -> None:
        self.session_name = session_name
        entries = await self._repository.entries_for(self.agent_name, session_name)
        await self.transcript.replace_entries(
            entries,
            inline_permission_cards=self._pending_permission_cards(session_name),
        )
        await self.sidebar.refresh_data()

    async def set_detail_level(self, detail_level: EventDetailLevel) -> None:
        self._detail_level = detail_level
        await self.transcript.set_detail_level(
            detail_level,
            inline_permission_cards=self._pending_permission_cards(self.session_name),
        )
        selector = self.query_one(".detail-filter", Select)
        if selector.value != detail_level.value:
            selector.value = detail_level.value

    async def refresh_sidebar(self) -> None:
        await self.sidebar.refresh_data()

    async def enqueue_permission(self, request: PermissionRequest) -> None:
        """Register a request as live UI state, scoped to its session and id."""
        key = (request.session_name, request.request_id)
        if key in self._permission_cards:
            return
        card = PermissionRequestCard(request, classes="permission-card")
        self._permission_cards[key] = card
        if request.session_name == self.session_name:
            await self.transcript.replace_entries(
                self.transcript.entries,
                inline_permission_cards=self._pending_permission_cards(self.session_name),
            )

    def resolve_permission(self, request: PermissionRequest, decision: str) -> None:
        card = self._permission_cards.get((request.session_name, request.request_id))
        if card is not None:
            card.resolve(decision)

    def allow_permission_retry(self, request: PermissionRequest) -> None:
        card = self._permission_cards.get((request.session_name, request.request_id))
        if card is not None:
            card.allow_retry()

    def _pending_permission_cards(
        self, session_name: str | None
    ) -> dict[int, PermissionRequestCard]:
        if session_name is None:
            return {}
        planned: dict[int, PermissionRequestCard] = {}
        for (card_session, request_id), entry_position in self._pending_permission_positions.items():
            if card_session != session_name:
                continue
            card = self._permission_cards.get((card_session, request_id))
            if card is not None:
                planned[entry_position] = card
        return planned

    @staticmethod
    def _pending_permission_key(
        session_name: str, entry: TranscriptEntry
    ) -> tuple[str, str] | None:
        if entry.metadata["type"] != "permission_request":
            return None
        return session_name, entry.metadata["request_id"]
