"""Async command dispatch for a selected agent session."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol

from pairo.core.agent import AgentState, ConfiguredMCPServer
from pairo.core.agent_config import AgentConfiguration, JsonObject
from pairo.core.events import EventPipeline, SessionUpdated

from .models import EventDetailLevel, TranscriptEntry
from .session_repository import SessionRepository


class ChatAgent(Protocol):
    """Agent contract required by the chat UI and application lifecycle."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str | None: ...

    @property
    def session_name(self) -> str | None: ...

    @property
    def session_names(self) -> tuple[str, ...]: ...

    @property
    def state(self) -> AgentState: ...

    @property
    def is_generating(self) -> bool: ...

    @property
    def configuration(self) -> AgentConfiguration: ...

    @property
    def configured_mcp_servers(self) -> tuple[ConfiguredMCPServer, ...]: ...

    async def send_message(self, message: str, source: str = "user") -> None: ...

    async def create_session(self, session_name: str) -> str: ...

    async def restore_session(
        self, session_name: str, identifier_json: JsonObject
    ) -> str: ...

    async def switch_session(self, session_name: str) -> str: ...

    async def cancel_generation(self) -> bool: ...

    async def run(self, initial_session_name: str) -> str: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    entry: TranscriptEntry | None
    session_name: str | None = None
    selected_agent: str | None = None


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
        "/agent NAME, /session new [NAME], /session switch NAME, /sessions, "
        "/interrupt, /image PATH, /help"
    )

    def __init__(
        self,
        agents: Mapping[str, ChatAgent],
        event_pipeline: EventPipeline,
        session_repository: SessionRepository,
    ) -> None:
        self._agents = dict(agents)
        self._event_pipeline = event_pipeline
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
        self._event_pipeline.add_listener(SessionUpdated, self._persist_session)

    @property
    def agents(self) -> Mapping[str, ChatAgent]:
        return MappingProxyType(self._agents)

    @property
    def event_pipeline(self) -> EventPipeline:
        return self._event_pipeline

    @property
    def session_repository(self) -> SessionRepository:
        return self._session_repository

    async def execute(self, agent_name: str, submitted: str) -> CommandOutcome:
        text = submitted.strip()
        if not text:
            raise ValueError("Message cannot be empty.")
        agent = self._agents[agent_name]
        if not text.startswith("/"):
            await agent.send_message(text)
            return CommandOutcome(
                None,
                session_name=agent.session_name,
            )

        command_name, _, argument = text.partition(" ")
        command = self._commands_by_name.get(command_name)
        if command is None:
            raise ValueError(f"Unknown command '{command_name}'. Use /help.")
        return await command.invoke(self, agent_name, argument.strip())

    async def select_session(self, agent_name: str, session_name: str) -> str:
        agent = self._agents[agent_name]
        if session_name in agent.session_names:
            return await agent.switch_session(session_name)
        metadata = await self._session_repository.get_session(agent_name, session_name)
        return await agent.restore_session(session_name, metadata.identifier)

    async def default_session_name(self, agent_name: str) -> str:
        persisted = await self._session_repository.sessions_for(agent_name)
        names = {session.session_name for session in persisted}
        names.update(self._agents[agent_name].session_names)
        index = 1
        while f"Conversation {index}" in names:
            index += 1
        return f"Conversation {index}"

    async def _persist_session(self, event: SessionUpdated) -> SessionUpdated:
        await self._session_repository.save_session(
            event.agent_name,
            event.session_name,
            event.identifier,
        )
        return event
