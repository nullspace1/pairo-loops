"""Agent information and session-selection sidebar."""

from datetime import datetime
from uuid import UUID

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Select, Static

from pairo.core.agent import Agent, Runner

from ..models import EventDetailLevel
from ..session_repository import SessionNotFoundError, SessionRepository
from .sidebar_field_group import SidebarFieldGroup


class AgentSidebar(Vertical):
    _AGENT_INFORMATION = "agent"
    _SESSION_INFORMATION = "session"

    class SessionSelected(Message):
        def __init__(self, agent_id: UUID, session_name: str) -> None:
            super().__init__()
            self.agent_id = agent_id
            self.session_name = session_name

    class SessionDeletionRequested(Message):
        def __init__(self, agent_id: UUID, session_name: str) -> None:
            super().__init__()
            self.agent_id = agent_id
            self.session_name = session_name

    def __init__(
        self,
        agent: Agent,
        runner: Runner,
        repository: SessionRepository,
        detail_level: EventDetailLevel,
        *,
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
            self.post_message(self.SessionSelected(self.agent.id, event.button.name))

    @on(Button.Pressed, ".session-delete-button")
    def delete_session(self, event: Button.Pressed) -> None:
        if event.button.name is not None:
            event.stop()
            self.post_message(
                self.SessionDeletionRequested(self.agent.id, event.button.name)
            )

    async def refresh_data(self) -> None:
        await self._refresh_information_fields()
        generating = self._runner.is_generating(self.agent.id)
        self.query_one(".session-switch-note", Static).update(
            "Finish or interrupt the current response to switch sessions."
            if generating
            else "Select a session to resume it."
        )
        sessions = sorted(
            await self._repository.sessions_for(self.agent.id),
            key=lambda session: session.last_updated_at,
            reverse=True,
        )
        snapshot = (
            generating,
            self._runner.session_name(self.agent.id),
            tuple((session.session_name, session.last_updated_at) for session in sessions),
        )
        if snapshot == self._session_snapshot:
            return
        self._session_snapshot = snapshot
        container = self.query_one(".session-list", Vertical)
        await container.remove_children()
        rows = [
            Horizontal(
                Button(
                    session.session_name,
                    name=session.session_name,
                    disabled=(
                        generating
                        or session.session_name == self._runner.session_name(self.agent.id)
                    ),
                    classes=(
                        "session-button current-session"
                        if session.session_name == self._runner.session_name(self.agent.id)
                        else "session-button"
                    ),
                ),
                Button(
                    "X",
                    name=session.session_name,
                    disabled=generating,
                    classes="session-delete-button",
                    variant="error",
                ),
                classes="session-row",
            )
            for session in sessions
        ]
        await container.mount(
            *(rows or [Static("No sessions yet.", classes="empty-session-list")])
        )

    async def _refresh_information_fields(self) -> None:
        fields = (
            self._agent_fields()
            if self._information_mode == self._AGENT_INFORMATION
            else await self._current_session_fields()
        )
        await self.query_one(".sidebar-information", SidebarFieldGroup).set_fields(fields)

    def _agent_fields(self) -> tuple[tuple[str, object], ...]:
        config = self.agent
        values = (
            ("Name", self.agent.name),
            ("ID", self.agent.id),
            (
                "State",
                getattr(
                    self._runner.state(self.agent.id),
                    "value",
                    self._runner.state(self.agent.id),
                ),
            ),
            ("Model", config.model),
            ("Reasoning", self._enum_value(config.reasoning_effort)),
            ("Sandbox", self._enum_value(config.sandbox)),
            ("Workspace", config.workspace),
            ("Approval mode", self._enum_value(config.approval_mode)),
            ("Web search", self._enum_value(config.web_search)),
            ("Prompt", config.base_instructions or "Not set"),
            ("Developer instructions", config.developer_instructions or "Not set"),
        )
        servers = self.agent.mcp_servers
        return values + (
            tuple((f"MCP server: {server.name}", server.description) for server in servers)
            if servers
            else (("MCP servers", "None configured"),)
        )

    async def _current_session_fields(self) -> tuple[tuple[str, object], ...]:
        session_name = self._runner.session_name(self.agent.id)
        if session_name is None:
            return (("Session", "No session selected."),)
        try:
            session = await self._repository.get_session(self.agent.id, session_name)
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
