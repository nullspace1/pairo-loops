"""High-level application lifecycle for a configured agent runner."""

from __future__ import annotations

from .core.agent import Runner
from .ui.app import run_ui
from .ui.session_repository import SessionRepository


async def run_agents(
    runner: Runner,
    session_repository: SessionRepository,
    *,
    initial_session_name: str = "Conversation 1",
) -> None:
    """Start conversations, run the UI, and release shared resources."""
    try:
        await runner.start(initial_session_name)
        await run_ui(runner, session_repository)
    finally:
        try:
            await runner.close()
        finally:
            try:
                await session_repository.close()
            finally:
                await runner.pipeline.close()


__all__ = ["run_agents"]
