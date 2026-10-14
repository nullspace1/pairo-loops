"""Async command dispatch through the shared agent runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from uuid import UUID

from pairo.core.agent import Agent, Runner
from pairo.core.events import EventPipeline, SessionDeleted, SessionUpdated

from .models import EventDetailLevel, TranscriptEntry
from .session_repository import SessionRepository


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    entry: TranscriptEntry | None
    session_name: str | None = None
    selected_agent: UUID | None = None


from .chat_commands import (  # noqa: E402
    AgentSelectionCommand,
    CompactCommand,
    DeleteCommand,
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
        "/compact, /delete [NAME], /interrupt, /image PATH, /help"
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
            CompactCommand(),
            DeleteCommand(),
            InterruptCommand(),
            ImageCommand(),
        ]
        self._commands_by_name = {command.name: command for command in self._commands}
        self._session_replay = self._runner.pipeline.add_listener(
            SessionUpdated,
            self._persist_session,
            process_existing_events=True,
        )

    async def wait_until_ready(self) -> None:
        """Wait until sessions published before construction are persisted."""
        if self._session_replay is not None:
            await self._session_replay
            self._session_replay = None

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

    async def delete_session(
        self,
        agent_id: UUID,
        session_name: str | None = None,
    ) -> tuple[str, str]:
        """Delete one session and guarantee that the agent still has a selection."""
        target = session_name or self._runner.session_name(agent_id)
        if target is None:
            raise RuntimeError("The selected agent has no active session.")

        loaded_names = self._runner.session_names(agent_id)
        current_name = self._runner.session_name(agent_id)
        selected_name = current_name
        if target in loaded_names:
            selected_name = await self._runner.delete_session(agent_id, target)
        else:
            await self._session_repository.get_session(agent_id, target)

        await self._session_repository.delete_session(agent_id, target)

        if selected_name is None:
            remaining = await self._session_repository.sessions_for(agent_id)
            if remaining:
                selected_name = await self.select_session(
                    agent_id,
                    remaining[-1].session_name,
                )
            else:
                selected_name = await self._runner.create_session(
                    agent_id,
                    await self.default_session_name(agent_id),
                )
        await self._runner.pipeline.enqueue(
            SessionDeleted(
                agent_id=agent_id,
                session_name=target,
                selected_session_name=selected_name,
            )
        )
        return target, selected_name

    async def _persist_session(self, event: SessionUpdated) -> SessionUpdated:
        await self._session_repository.save_session(
            event.agent_id,
            event.session_name,
            event.identifier,
        )
        return event
