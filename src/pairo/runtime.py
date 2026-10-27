"""High-level lifecycle for a collection of asynchronous agents."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import cast
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import ActiveSession, Agent
from pairo.core.events import EventPipeline
from pairo.ui.app import run_ui
from pairo.ui.session_repository import SessionRepository


AgentMapping = Mapping[UUID, Agent[SessionIdentifier]]
UserInterfaceLauncher = Callable[
    [AgentMapping, EventPipeline, SessionRepository],
    Awaitable[None],
]


class RuntimeLauncher:
    """Start all agents and reliably close application-owned resources."""

    def __init__(
        self,
        agents: AgentMapping,
        event_pipeline: EventPipeline,
        session_repository: SessionRepository,
        user_interface_launcher: UserInterfaceLauncher,
    ) -> None:
        configured = dict(agents)
        if not configured:
            raise ValueError("At least one agent is required.")
        for agent_id, agent in configured.items():
            if agent.configuration().id != agent_id:
                raise ValueError(f"Agent mapping key '{agent_id}' does not match its agent ID.")
        self._agents: AgentMapping = MappingProxyType(configured)
        self._event_pipeline = event_pipeline
        self._session_repository = session_repository
        self._user_interface_launcher = user_interface_launcher

    async def launch(self, initial_session_name: str = "Conversation 1") -> None:
        try:
            startup_results = await asyncio.gather(
                *(
                    self._activate_or_create_session(
                        agent_id, agent, initial_session_name
                    )
                    for agent_id, agent in self._agents.items()
                ),
                return_exceptions=True,
            )
            startup_failure = next(
                (
                    result
                    for result in startup_results
                    if isinstance(result, BaseException)
                ),
                None,
            )
            if startup_failure is not None:
                raise startup_failure
            for agent_id, result in zip(self._agents, startup_results):
                if result is None:
                    continue
                active = cast(ActiveSession[SessionIdentifier], result)
                await self._session_repository.save_session(
                    agent_id,
                    active.name,
                    active.identifier.model_dump(mode="json"),
                )
            await self._user_interface_launcher(
                self._agents,
                self._event_pipeline,
                self._session_repository,
            )
        finally:
            await self._close_resources()

    async def _activate_or_create_session(
        self,
        agent_id: UUID,
        agent: Agent[SessionIdentifier],
        fallback_session_name: str,
    ) -> ActiveSession[SessionIdentifier] | None:
        try:
            latest = await self._session_repository.latest_session_for(agent_id)
            if latest is not None:
                await agent.activate_session(latest.session_name, latest.identifier)
                return None
            return await agent.new_session(fallback_session_name)
        except Exception:
            return await agent.new_session(fallback_session_name)

    async def _close_resources(self) -> None:
        try:
            results = await asyncio.gather(
                *(agent.close() for agent in self._agents.values()),
                return_exceptions=True,
            )
            failure = next(
                (result for result in results if isinstance(result, BaseException)),
                None,
            )
            if failure is not None:
                raise failure
        finally:
            await self._session_repository.close()


class RunAgents:
    """Callable public entry point for the complete application lifecycle."""

    async def __call__(
        self,
        agents: AgentMapping,
        event_pipeline: EventPipeline,
        session_repository: SessionRepository,
        *,
        initial_session_name: str = "Conversation 1",
    ) -> None:
        launcher = RuntimeLauncher(
            agents,
            event_pipeline,
            session_repository,
            run_ui,
        )
        await launcher.launch(initial_session_name)


run_agents = RunAgents()


__all__ = ["AgentMapping", "RunAgents", "RuntimeLauncher", "run_agents"]
