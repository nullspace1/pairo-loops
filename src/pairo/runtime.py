"""High-level application lifecycle for a configured agent runner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .core.agent import Runner
from .ui.app import run_ui
from .ui.session_repository import SessionRepository


UserInterfaceLauncher = Callable[[Runner, SessionRepository], Awaitable[None]]


class RuntimeLauncher:
    """Launch one runner and reliably release its shared resources."""

    def __init__(
        self,
        runner: Runner,
        session_repository: SessionRepository,
        user_interface_launcher: UserInterfaceLauncher,
    ) -> None:
        self._runner = runner
        self._session_repository = session_repository
        self._user_interface_launcher = user_interface_launcher

    async def launch(self, initial_session_name: str = "Conversation 1") -> None:
        """Start the runner, host its UI, then close all owned resources."""
        try:
            await self._runner.start(initial_session_name)
            await self._user_interface_launcher(self._runner, self._session_repository)
        finally:
            await self._close_resources()

    async def _close_resources(self) -> None:
        try:
            await self._runner.close()
        finally:
            try:
                await self._session_repository.close()
            finally:
                await self._runner.pipeline.close()


async def run_agents(
    runner: Runner,
    session_repository: SessionRepository,
    *,
    initial_session_name: str = "Conversation 1",
) -> None:
    """Compatibility entry point for launching the configured application."""
    launcher = RuntimeLauncher(runner, session_repository, run_ui)
    await launcher.launch(initial_session_name)


__all__ = ["RuntimeLauncher", "run_agents"]

