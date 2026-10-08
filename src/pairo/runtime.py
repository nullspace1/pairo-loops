"""High-level application lifecycle for Pairo agents."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from .core.events import EventPipeline
from .ui.app import run_ui
from .ui.commands import ChatAgent
from .ui.session_repository import SessionRepository


async def run_agents(
    agents: Sequence[ChatAgent],
    event_pipeline: EventPipeline,
    session_repository: SessionRepository,
    *,
    initial_session_name: str = "Conversation 1",
) -> None:
    """Start agents, operate the Textual UI, and release shared resources.

    The supplied agents must use ``event_pipeline`` and any permission requesters
    associated with them must use ``session_repository``. Both shared resources are
    closed after the UI exits, including when startup or the UI raises an error.
    """
    managed_agents = tuple(agents)
    if not managed_agents:
        raise ValueError("run_agents requires at least one agent.")

    try:
        await asyncio.gather(
            *(agent.run(initial_session_name) for agent in managed_agents)
        )
        await run_ui(managed_agents, event_pipeline, session_repository)
    finally:
        try:
            await asyncio.gather(*(agent.close() for agent in managed_agents))
        finally:
            try:
                await session_repository.close()
            finally:
                await event_pipeline.close()
