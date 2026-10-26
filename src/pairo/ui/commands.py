"""Asynchronous command dispatch through provider-neutral agents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import EventPipeline

from .session_repository import SessionRepository

from .chat_commands.base import SlashCommand


class AgentCommandController:
    def __init__(
        self,
        agents: Mapping[UUID, Agent[SessionIdentifier]],
        event_pipeline: EventPipeline,
        session_repository: SessionRepository,
        commands: Sequence[SlashCommand],
    ) -> None:
        self._agents = agents
        self._event_pipeline = event_pipeline
        self._session_repository = session_repository
        self._commands = tuple(commands)
        self._validate_command_names()
        self._commands_by_name = {command.name: command for command in self._commands}

    def _validate_command_names(self) -> None:
        names = [command.name for command in self._commands]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(
                "Duplicate slash command names: " + ", ".join(duplicates) + "."
            )

    def agent(self, agent_id: UUID) -> Agent[SessionIdentifier]:
        try:
            return self._agents[agent_id]
        except KeyError as error:
            raise KeyError(f"Agent '{agent_id}' is not configured.") from error

    async def execute(self, agent_id: UUID, submitted: str) -> None:
        text = submitted.strip()
        if not text:
            raise ValueError("Message cannot be empty.")
        agent = self.agent(agent_id)
        if not text.startswith("/"):
            await agent.send_message(text)
            return
        command_name, _, argument = text.partition(" ")
        command = self._commands_by_name.get(command_name)
        if command is None:
            raise ValueError(f"Unknown command '{command_name}'. Use /help.")
        await command.invoke(
            self._event_pipeline,
            agent,
            self._agents,
            self._session_repository,
            argument.strip(),
        )
