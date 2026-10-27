"""Configuration object for composing the Textual user interface."""

from __future__ import annotations

from collections.abc import Sequence

from .chat_commands.base import SlashCommand
from .session_repository import SessionRepository
from .transcribers import TranscriberRegistry
from .widgets.sidebar_statistics import SidebarStatistic


class UserInterfaceConfiguration:
    """Mutable builder for collaborators installed into one UI launch.

    Registrations are snapshotted by :class:`UserInterfaceLauncher`; configure
    this object before starting the launcher rather than mutating it while the
    UI is running.
    """

    def __init__(
        self,
        session_repository: SessionRepository | None = None,
        *,
        transcribers: TranscriberRegistry | None = None,
        sidebar_statistics: Sequence[SidebarStatistic] | None = None,
        include_default_commands: bool = True,
    ) -> None:
        self._session_repository = session_repository
        self._transcribers = transcribers
        self._sidebar_statistics = None if sidebar_statistics is None else tuple(sidebar_statistics)
        self._include_default_commands = include_default_commands
        self._commands: list[SlashCommand] = []

    @property
    def session_repository(self) -> SessionRepository | None:
        return self._session_repository

    @property
    def transcribers(self) -> TranscriberRegistry | None:
        return self._transcribers

    @property
    def sidebar_statistics(self) -> tuple[SidebarStatistic, ...] | None:
        return self._sidebar_statistics

    @property
    def include_default_commands(self) -> bool:
        return self._include_default_commands

    @property
    def commands(self) -> Sequence[SlashCommand]:
        return tuple(self._commands)

    def add_command(self, command: SlashCommand) -> UserInterfaceConfiguration:
        """Append a command after the launcher supplied commands."""
        self._commands.append(command)
        return self

    def use_session_repository(
        self, repository: SessionRepository
    ) -> UserInterfaceConfiguration:
        self._session_repository = repository
        return self

    def use_transcribers(
        self, transcribers: TranscriberRegistry
    ) -> UserInterfaceConfiguration:
        self._transcribers = transcribers
        return self

    def use_sidebar_statistics(
        self, statistics: Sequence[SidebarStatistic]
    ) -> UserInterfaceConfiguration:
        self._sidebar_statistics = tuple(statistics)
        return self

    def without_default_commands(self) -> UserInterfaceConfiguration:
        """Use only commands added explicitly to this configuration."""
        self._include_default_commands = False
        return self


__all__ = ["UserInterfaceConfiguration"]
