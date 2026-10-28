"""High-level lifecycle for a collection of asynchronous agents."""

from __future__ import annotations

import asyncio
from typing import cast

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import ActiveSession, Agent
from pairo.core.events import EventPipeline
from pairo.ui.app import run_ui
from pairo.ui.session_repository import SessionRepository
from pairo.ui.sqlite_session_repository import SqliteSessionRepository
from pairo.ui.transcribers import TranscriberRegistry
from pairo.ui.widgets.sidebar_statistics import SidebarStatistic


async def run_agents(
    agents: list[Agent[SessionIdentifier]],
    event_pipeline: EventPipeline,
    session_repository: SessionRepository | None = None,
    *,
    transcribers: TranscriberRegistry | None = None,
    sidebar_statistics: list[SidebarStatistic] | None = None,
    initial_session_name: str = "Conversation 1",
) -> None:
    """Start agents, launch the UI, and close the owned resources."""
    configured = list(agents)
    if not configured:
        raise ValueError("At least one agent is required.")
    ids = [agent.configuration().id for agent in configured]
    if len(set(ids)) != len(ids):
        raise ValueError("Agent IDs must be unique.")

    repository = session_repository if session_repository is not None else SqliteSessionRepository()

    async def activate_or_create(agent: Agent[SessionIdentifier]) -> ActiveSession[SessionIdentifier] | None:
        try:
            latest = await repository.latest_session_for(agent.configuration().id)
            if latest is not None:
                await agent.activate_session(latest.session_name, latest.identifier)
                return None
        except Exception:
            pass
        return await agent.new_session(initial_session_name)

    try:
        results = await asyncio.gather(
            *(activate_or_create(agent) for agent in configured), return_exceptions=True
        )
        failure = next((result for result in results if isinstance(result, BaseException)), None)
        if failure is not None:
            raise failure
        for agent, result in zip(configured, results):
            if result is not None:
                active = cast(ActiveSession[SessionIdentifier], result)
                await repository.save_session(
                    agent.configuration().id, active.name, active.identifier.model_dump(mode="json")
                )
        await run_ui(
            {agent.configuration().id: agent for agent in configured},
            event_pipeline,
            repository,
            transcribers=transcribers,
            sidebar_statistics=sidebar_statistics,
        )
    finally:
        try:
            close_results = await asyncio.gather(
                *(agent.close() for agent in configured), return_exceptions=True
            )
            failure = next(
                (result for result in close_results if isinstance(result, BaseException)), None
            )
            if failure is not None:
                raise failure
        finally:
            await repository.close()


__all__ = ["run_agents"]
