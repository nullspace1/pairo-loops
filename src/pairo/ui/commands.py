"""Asynchronous command dispatch through provider-neutral agents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import EventPipeline, SessionDeleted

from .models import EventDetailLevel, TranscriptEntry, TranscriptMetadata
from .session_repository import SessionRepository


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    entry: TranscriptEntry[TranscriptMetadata] | None
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
        agents: Mapping[UUID, Agent[SessionIdentifier]],
        event_pipeline: EventPipeline,
        session_repository: SessionRepository,
    ) -> None:
        self._agents = agents
        self._event_pipeline = event_pipeline
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

    @property
    def agents(self) -> Mapping[UUID, Agent[SessionIdentifier]]:
        return self._agents

    @property
    def event_pipeline(self) -> EventPipeline:
        return self._event_pipeline

    @property
    def session_repository(self) -> SessionRepository:
        return self._session_repository

    def agent(self, agent_id: UUID) -> Agent[SessionIdentifier]:
        try:
            return self._agents[agent_id]
        except KeyError as error:
            raise KeyError(f"Agent '{agent_id}' is not configured.") from error

    def current_session_name(self, agent_id: UUID) -> str | None:
        active = self.agent(agent_id).current_session()
        return active.name if active is not None else None

    async def execute(self, agent_id: UUID, submitted: str) -> CommandOutcome:
        text = submitted.strip()
        if not text:
            raise ValueError("Message cannot be empty.")
        if not text.startswith("/"):
            agent = self.agent(agent_id)
            await agent.send_message(text)
            return CommandOutcome(None, session_name=self.current_session_name(agent_id))
        command_name, _, argument = text.partition(" ")
        command = self._commands_by_name.get(command_name)
        if command is None:
            raise ValueError(f"Unknown command '{command_name}'. Use /help.")
        return await command.invoke(self, agent_id, argument.strip())

    async def create_session(self, agent_id: UUID, session_name: str) -> str:
        active = await self.agent(agent_id).new_session(session_name)
        await self._session_repository.save_session(
            agent_id,
            active.name,
            active.identifier.model_dump(mode="json"),
        )
        return active.name

    async def select_session(self, agent_id: UUID, session_name: str) -> str:
        agent = self.agent(agent_id)
        active = agent.current_session()
        if active is not None and active.name == session_name:
            return active.name
        metadata = await self._session_repository.get_session(agent_id, session_name)
        active = await agent.activate_session(session_name, metadata.identifier)
        await self._session_repository.save_session(
            agent_id,
            active.name,
            active.identifier.model_dump(mode="json"),
        )
        return active.name

    async def default_session_name(self, agent_id: UUID) -> str:
        persisted = await self._session_repository.sessions_for(agent_id)
        names = {session.session_name for session in persisted}
        index = 1
        while f"Conversation {index}" in names:
            index += 1
        return f"Conversation {index}"

    async def delete_session(
        self,
        agent_id: UUID,
        session_name: str | None = None,
    ) -> tuple[str, str]:
        agent = self.agent(agent_id)
        active = agent.current_session()
        target = session_name or (active.name if active is not None else None)
        if target is None:
            raise RuntimeError("The selected agent has no active session.")
        persisted = await self._session_repository.sessions_for(agent_id)
        if target not in {session.session_name for session in persisted}:
            await self._session_repository.get_session(agent_id, target)

        if active is not None and active.name != target:
            selected = active.name
        else:
            remaining = [
                session for session in persisted if session.session_name != target
            ]
            if remaining:
                selected = await self.select_session(
                    agent_id,
                    remaining[-1].session_name,
                )
            else:
                selected = await self.create_session(
                    agent_id,
                    await self.default_session_name(agent_id),
                )
        await self._session_repository.delete_session(agent_id, target)
        self._event_pipeline.push(
            SessionDeleted(
                agent_id=agent_id,
                session_name=target,
                selected_session_name=selected,
            )
        )
        return target, selected
