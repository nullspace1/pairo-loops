"""Textual message-pump sidebar statistic primitives."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from textual.app import App
from textual.message import Message
from textual.message_pump import MessagePump

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import ActiveSession, Agent
from pairo.ui.models import SessionMetadata


class SidebarStatisticCategory(StrEnum):
    AGENT = "agent"
    SESSION = "session"


@dataclass(frozen=True)
class SidebarStatisticContext:
    """Typed snapshot used to compute one sidebar value."""
    agent: Agent[SessionIdentifier]
    active_session: ActiveSession[SessionIdentifier] | None
    session: SessionMetadata | None
    @property
    def session_name(self) -> str | None:
        return None if self.active_session is None else self.active_session.name


class SaveAgentStatistic(Message, bubble=False):
    def __init__(self, key: str, value: object, agent: Agent[SessionIdentifier]) -> None:
        self.key, self.value, self.agent = key, value, agent
        super().__init__()


class SaveSessionStatistic(Message, bubble=False):
    def __init__(self, key: str, value: object, agent: Agent[SessionIdentifier], session: ActiveSession[SessionIdentifier]) -> None:
        self.key, self.value, self.agent, self.session = key, value, agent, session
        super().__init__()


class SidebarStatistic(MessagePump):
    """A connected Textual owner for one asynchronous sidebar field."""
    def __init__(self, key: str, label: str, category: SidebarStatisticCategory) -> None:
        super().__init__()
        self.key = key
        self.label = label
        self.category = category
        self._statistic_context: SidebarStatisticContext | None = None
        self._connected = False

    async def setup(self, app: App[None], context: SidebarStatisticContext) -> None:
        """Connect once to Textual; event ingress remains application-owned."""
        if self._connected:
            if self._statistic_context is not None and self._statistic_context.agent is context.agent:
                return
            raise RuntimeError("Sidebar statistic is already connected.")
        self._parent = app
        self._connected = True
        self._start_messages()
        await self.activate(context)

    async def activate(self, context: SidebarStatisticContext) -> None:
        self._statistic_context = context
        await self.refresh()

    async def teardown(self) -> None:
        if not self._connected:
            return
        self._connected = False
        await self._close_messages()

    async def compute(self, context: SidebarStatisticContext) -> object:
        """Compute the current value from the strongly typed context."""
        raise NotImplementedError

    async def refresh(self) -> None:
        context = self._statistic_context
        if context is None or not self._connected:
            return
        value = await self.compute(context)
        if self.category is SidebarStatisticCategory.AGENT:
            self.app.post_message(SaveAgentStatistic(self.key, value, context.agent))
        elif context.active_session is not None:
            self.app.post_message(SaveSessionStatistic(self.key, value, context.agent, context.active_session))


__all__ = ["SaveAgentStatistic", "SaveSessionStatistic", "SidebarStatistic", "SidebarStatisticCategory", "SidebarStatisticContext"]

