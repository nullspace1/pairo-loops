"""Async command dispatch through the shared agent runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from uuid import UUID

from pairo.core.agent import Agent, Runner
from pairo.core.events import EventPipeline, SessionUpdated

from .models import EventDetailLevel, TranscriptEntry
from .session_repository import SessionRepository


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    entry: TranscriptEntry | None
    session_name: str | None = None
    selected_agent: UUID | None = None


from .chat_commands import (  # noqa: E402
    AgentSelectionCommand,
    HelpCommand,
    ImageCommand,
    InterruptCommand,
    SessionCommand,
    SessionsCommand,
    SlashCommand,
)


class AgentCommandController:
    HELP = (
        "/agent ID, /session new [NAME], /session switch NAME, /sessions, "
        "/interrupt, /image PATH, /help"
    )

    def __init__(
        self,
        runner: Runner,
        session_repository: SessionRepository,
    ) -> None:
        self._runner = runner
        self._session_repository = session_repository
        self._commands: list[SlashCommand] = [
            HelpCommand(),
            AgentSelectionCommand(),
            SessionCommand(),
            SessionsCommand(),
            InterruptCommand(),
            ImageCommand(),
        ]
        self._commands_by_name = {command.name: command for command in self._commands}
        self._runner.pipeline.add_listener(SessionUpdated, self._persist_session)

    @property
    def agents(self) -> Mapping[UUID, Agent]:
        return self._runner.agents

    @property
    def runner(self) -> Runner:
        return self._runner

    @property
    def event_pipeline(self) -> EventPipeline:
        return self._runner.pipeline

    @property
    def session_repository(self) -> SessionRepository:
        return self._session_repository

    async def execute(self, agent_id: UUID, submitted: str) -> CommandOutcome:
        text = submitted.strip()
        if not text:
            raise ValueError("Message cannot be empty.")
        if not text.startswith("/"):
            await self._runner.send_message(agent_id, text)
            return CommandOutcome(
                None,
                session_name=self._runner.session_name(agent_id),
            )

        command_name, _, argument = text.partition(" ")
        command = self._commands_by_name.get(command_name)
        if command is None:
            raise ValueError(f"Unknown command '{command_name}'. Use /help.")
        return await command.invoke(self, agent_id, argument.strip())

    async def select_session(self, agent_id: UUID, session_name: str) -> str:
        if session_name in self._runner.session_names(agent_id):
            return await self._runner.switch_session(agent_id, session_name)
        metadata = await self._session_repository.get_session(agent_id, session_name)
        return await self._runner.restore_session(
            agent_id,
            session_name,
            metadata.identifier,
        )

    async def default_session_name(self, agent_id: UUID) -> str:
        persisted = await self._session_repository.sessions_for(agent_id)
        names = {session.session_name for session in persisted}
        names.update(self._runner.session_names(agent_id))
        index = 1
        while f"Conversation {index}" in names:
            index += 1
        return f"Conversation {index}"

    async def _persist_session(self, event: SessionUpdated) -> SessionUpdated:
        await self._session_repository.save_session(
            event.agent_id,
            event.session_name,
            event.identifier,
        )
        return event
