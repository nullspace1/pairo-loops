"""Textual message-pump sidebar statistic primitives."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from collections.abc import Iterable
from uuid import UUID

from textual.app import App
from textual.message import Message
from textual.message_pump import MessagePump

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import ActiveSession, Agent
from pairo.ui.models import SessionMetadata
from pairo.ui.session_repository import SessionRepository, StatisticValue


class SidebarStatisticCategory(StrEnum):
    AGENT = "agent"
    SESSION = "session"


@dataclass(frozen=True)
class SidebarStatisticContext:
    agent: Agent[SessionIdentifier]
    active_session: ActiveSession[SessionIdentifier] | None
    session: SessionMetadata | None

    @property
    def session_name(self) -> str | None:
        return None if self.active_session is None else self.active_session.name


class SaveAgentStatistic(Message, bubble=False):
    def __init__(self, key: str, value: str, agent: Agent[SessionIdentifier]) -> None:
        self.key, self.value, self.agent = key, value, agent
        super().__init__()


class SaveSessionStatistic(Message, bubble=False):
    def __init__(self, key: str, value: str, agent: Agent[SessionIdentifier], session: ActiveSession[SessionIdentifier]) -> None:
        self.key, self.value, self.agent, self.session = key, value, agent, session
        super().__init__()


class SidebarStatistic(MessagePump):
    """One app-owned, repository-backed statistic message pump."""

    def __init__(self, key: str, label: str, category: SidebarStatisticCategory) -> None:
        super().__init__()
        self.key, self.label, self.category = key, label, category
        self._statistic_contexts: dict[UUID, SidebarStatisticContext] = {}
        self._repository: SessionRepository | None = None
        self._configured_agents: dict[UUID, Agent[SessionIdentifier]] = {}
        self._connected = False

    async def configure(
        self,
        repository: SessionRepository,
        app: App[None],
        agents: Iterable[Agent[SessionIdentifier]],
    ) -> None:
        """Attach this pump once to its owning app and configured agent set."""
        configured_agents = {
            agent.configuration().id: agent for agent in agents
        }
        if self._connected:
            if (
                self._repository is repository
                and self._configured_agents == configured_agents
                and self._parent is app
            ):
                return
            raise RuntimeError("Sidebar statistic already has an owner.")
        self._repository, self._configured_agents, self._parent = (
            repository,
            configured_agents,
            app,
        )
        self._connected = True
        self._start_messages()

    async def build(self, context: SidebarStatisticContext) -> None:
        """Compute and commit this statistic for a lifecycle snapshot.

        Build is deliberately silent: sidebar maps are reloaded by the app after
        every deterministic build pass.
        """
        self._require_context_owner(context)
        self._statistic_contexts[context.agent.configuration().id] = context
        if await self.has_initial_value(context):
            await self._persist(await self.compute(context), context, post=False)

    async def has_initial_value(self, context: SidebarStatisticContext) -> bool:
        """Whether this build owns an authoritative durable value.

        Stateful statistics may return ``False`` to preserve an existing value
        until a live event provides new data.
        """
        return True

    async def refresh(self, context: SidebarStatisticContext) -> None:
        """Commit a live event update before notifying the active sidebar."""
        if not self._connected:
            return
        self._require_context_owner(context)
        await self._persist(await self.compute(context), context, post=True)

    async def teardown(self) -> None:
        if self._connected:
            self._connected = False
            await self._close_messages()

    async def compute(self, context: SidebarStatisticContext) -> object | None:
        raise NotImplementedError

    def _require_context_owner(self, context: SidebarStatisticContext) -> None:
        agent_id = context.agent.configuration().id
        if (
            not self._connected
            or self._configured_agents.get(agent_id) is not context.agent
        ):
            raise RuntimeError("Sidebar statistic is not configured for this agent.")

    def _context_for(self, agent_id: UUID) -> SidebarStatisticContext | None:
        return self._statistic_contexts.get(agent_id)

    async def _persist(
        self,
        value: object | None,
        context: SidebarStatisticContext,
        *,
        post: bool,
    ) -> None:
        repository = self._repository
        if repository is None:
            raise RuntimeError("Sidebar statistic is not configured.")
        agent_id = context.agent.configuration().id
        if self.category is SidebarStatisticCategory.AGENT:
            if value is None:
                await repository.remove_agent_statistic(agent_id, self.key)
                return
            rendered = str(value)
            await repository.upsert_agent_statistic(agent_id, StatisticValue(self.key, rendered))
            if post:
                self.app.post_message(SaveAgentStatistic(self.key, rendered, context.agent))
            return
        session = context.active_session
        if session is None:
            return
        if value is None:
            await repository.remove_session_statistic(agent_id, session.name, self.key)
            return
        rendered = str(value)
        await repository.upsert_session_statistic(agent_id, session.name, StatisticValue(self.key, rendered))
        if post:
            self.app.post_message(SaveSessionStatistic(self.key, rendered, context.agent, session))


__all__ = ["SaveAgentStatistic", "SaveSessionStatistic", "SidebarStatistic", "SidebarStatisticCategory", "SidebarStatisticContext"]
