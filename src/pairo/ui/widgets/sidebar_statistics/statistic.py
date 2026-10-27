"""Scoped sidebar statistic primitives."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from textual.app import App
from textual.message import Message
from textual.message_pump import MessagePump

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.ui.session_repository import (
    SessionNotFoundError,
    SessionRepository,
    StatisticValue,
)


class SidebarStatisticCategory(StrEnum):
    AGENT = "agent"
    SESSION = "session"


@dataclass(frozen=True, slots=True)
class SidebarFieldDefinition:
    """The stable display definition derived from one statistic."""

    key: str
    label: str


class UpdateAgentStatistic(Message, bubble=False):
    """A persisted agent-scoped value is ready for the matching sidebar."""

    def __init__(self, key: str, value: str | None, agent_id: UUID) -> None:
        self.key = key
        self.value = value
        self.agent_id = agent_id
        super().__init__()


class UpdateSessionStatistic(Message, bubble=False):
    """A persisted session-scoped value is ready for the matching sidebar."""

    def __init__(
        self, key: str, value: str | None, agent_id: UUID, session_name: str
    ) -> None:
        self.key = key
        self.value = value
        self.agent_id = agent_id
        self.session_name = session_name
        super().__init__()


class SidebarStatistic(MessagePump):
    """One shared statistic with scoped durable reads and explicit updates."""

    def __init__(self, key: str, label: str, category: SidebarStatisticCategory) -> None:
        super().__init__()
        if not key.strip():
            raise ValueError("Sidebar statistic key cannot be empty.")
        self.key = key
        self.label = label
        self.category = category
        self._repository: SessionRepository | None = None
        self._configured_agents: dict[UUID, Agent[SessionIdentifier]] = {}
        self._connected = False

    @property
    def field(self) -> SidebarFieldDefinition:
        return SidebarFieldDefinition(self.key, self.label)

    async def configure(
        self,
        repository: SessionRepository,
        app: App[None],
        agents: Iterable[Agent[SessionIdentifier]],
    ) -> None:
        """Attach the shared statistic to its repository, app, and agent lookup."""
        configured_agents = {agent.configuration().id: agent for agent in agents}
        if self._connected:
            if (
                self._repository is repository
                and self._configured_agents == configured_agents
                and self._parent is app
            ):
                return
            raise RuntimeError("Sidebar statistic already has an owner.")
        self._repository = repository
        self._configured_agents = configured_agents
        self._parent = app
        self._connected = True
        self._start_messages()

    async def value(self, agent_id: UUID, session_name: str | None) -> str | None:
        """Read the latest durable rendered value for one supplied scope.

        Reads are deliberately side-effect free: selection/navigation cannot
        create or overwrite statistic rows.
        """
        repository = self._require_repository()
        self._require_configured_agent(agent_id)
        if self.category is SidebarStatisticCategory.AGENT:
            return self._value_for(await repository.agent_statistics_for(agent_id))
        if session_name is None:
            return None
        return self._value_for(
            await repository.session_statistics_for(agent_id, session_name)
        )

    async def update_agent(self, agent_id: UUID, value: str | None) -> None:
        """Persist and publish an explicit agent-scoped update."""
        if self.category is not SidebarStatisticCategory.AGENT:
            raise ValueError("A session statistic cannot be updated for an agent.")
        repository = self._require_repository()
        self._require_configured_agent(agent_id)
        if value is None:
            await repository.remove_agent_statistic(agent_id, self.key)
        else:
            await repository.upsert_agent_statistic(
                agent_id, StatisticValue(self.key, value)
            )
        self.app.post_message(UpdateAgentStatistic(self.key, value, agent_id))

    async def update_session(
        self, agent_id: UUID, session_name: str, value: str | None
    ) -> None:
        """Persist and publish an explicit session-scoped update."""
        if self.category is not SidebarStatisticCategory.SESSION:
            raise ValueError("An agent statistic cannot be updated for a session.")
        repository = self._require_repository()
        self._require_configured_agent(agent_id)
        if value is None:
            await repository.remove_session_statistic(agent_id, session_name, self.key)
        else:
            await repository.upsert_session_statistic(
                agent_id, session_name, StatisticValue(self.key, value)
            )
        self.app.post_message(
            UpdateSessionStatistic(self.key, value, agent_id, session_name)
        )

    async def teardown(self) -> None:
        if self._connected:
            self._connected = False
            await self._close_messages()

    def configured_agent(self, agent_id: UUID) -> Agent[SessionIdentifier]:
        """Return an agent from the immutable configuration lookup."""
        return self._require_configured_agent(agent_id)

    def _require_repository(self) -> SessionRepository:
        if self._repository is None:
            raise RuntimeError("Sidebar statistic is not configured.")
        return self._repository

    def _require_configured_agent(self, agent_id: UUID) -> Agent[SessionIdentifier]:
        agent = self._configured_agents.get(agent_id)
        if not self._connected or agent is None:
            raise RuntimeError("Sidebar statistic is not configured for this agent.")
        return agent

    def _value_for(self, values: tuple[StatisticValue, ...]) -> str | None:
        for value in values:
            if value.key == self.key:
                return value.value
        return None


__all__ = [
    "SidebarFieldDefinition",
    "SidebarStatistic",
    "SidebarStatisticCategory",
    "UpdateAgentStatistic",
    "UpdateSessionStatistic",
]
