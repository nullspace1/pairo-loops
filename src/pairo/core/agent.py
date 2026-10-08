"""Async provider-neutral lifecycle for one specialized agent."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum

from .agent_config import AgentConfiguration, JsonObject
from .agent_server import AgentClient, SessionIdentifier
from .events import (
    AgentError,
    AgentEvent,
    ClientResponseCompleted,
    ClientResponseInterrupted,
    EventPipeline,
    MessageSent,
    SessionUpdated,
)


class AgentState(str, Enum):
    IDLE = "idle"
    GENERATING = "generating"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class ConfiguredMCPServer:
    name: str
    endpoint: str


class Agent:
    """Coordinate named sessions using only the AgentClient protocol."""

    def __init__(
        self,
        *,
        name: str,
        config: AgentConfiguration,
        client: AgentClient,
        event_pipeline: EventPipeline,
        description: str | None = None,
    ) -> None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Agent name cannot be empty.")
        self._name = normalized_name
        self._description = description
        self._configuration = config
        self._client = client
        self._event_pipeline = event_pipeline
        self._sessions: dict[str, SessionIdentifier] = {}
        self._session_name: str | None = None
        self._messages: asyncio.Queue[str | None] = asyncio.Queue()
        self._completed_messages: asyncio.Queue[str | Exception | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._state = AgentState.IDLE
        self._state_lock = asyncio.Lock()
        self._cancel_requested = False
        self._last_message: str | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def session_name(self) -> str | None:
        return self._session_name

    @property
    def session_names(self) -> tuple[str, ...]:
        return tuple(self._sessions)

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def is_generating(self) -> bool:
        return self._state is AgentState.GENERATING

    @property
    def prompt(self) -> str | None:
        return self._configuration.prompt

    @property
    def configuration(self) -> AgentConfiguration:
        # The model is frozen; returning it avoids deep-copying runtime
        # collaborators such as permission requesters and repositories.
        return self._configuration

    @property
    def configured_mcp_servers(self) -> tuple[ConfiguredMCPServer, ...]:
        return tuple(
            ConfiguredMCPServer(server.name, server.description)
            for server in sorted(
                self._configuration.mcp_servers,
                key=lambda item: item.name.lower(),
            )
        )

    def last_message(self) -> str | None:
        return self._last_message

    def is_active(self) -> bool:
        return (
            self._session_name is not None
            and self._worker is not None
            and not self._worker.done()
        )

    def session_identifier_json(self, session_name: str) -> JsonObject:
        identifier = self._sessions[self._validated_session_name(session_name)]
        return identifier.model_dump(mode="json")

    async def run(self, initial_session_name: str) -> str:
        if self._state is AgentState.STOPPED:
            raise RuntimeError("The agent is stopped.")
        if self._worker is not None:
            raise RuntimeError("The agent is already running.")
        await self.create_session(initial_session_name)
        self._worker = asyncio.create_task(
            self._process_messages(),
            name=f"agent-{self.name}",
        )
        return self._require_session_name()

    async def create_session(self, session_name: str) -> str:
        name = self._validated_session_name(session_name)
        self._require_idle()
        if name in self._sessions:
            raise ValueError(f"Session '{name}' already exists for agent '{self.name}'.")
        identifier = await self._client.new_session(
            self._configuration,
            agent_name=self.name,
        )
        self._sessions[name] = identifier
        self._session_name = name
        await self._publish_session_update(name, identifier)
        return name

    async def restore_session(
        self,
        session_name: str,
        identifier_json: JsonObject,
    ) -> str:
        name = self._validated_session_name(session_name)
        self._require_idle()
        if name in self._sessions:
            raise ValueError(f"Session '{name}' is already loaded.")
        identifier = await self._client.restore_session(
            self._configuration,
            identifier_json,
        )
        self._sessions[name] = identifier
        self._session_name = name
        await self._publish_session_update(name, identifier)
        return name

    async def switch_session(self, session_name: str) -> str:
        name = self._validated_session_name(session_name)
        self._require_idle()
        if name not in self._sessions:
            raise KeyError(f"Session '{name}' is not loaded for agent '{self.name}'.")
        self._session_name = name
        return name

    async def send_message(self, message: str, source: str = "user") -> None:
        if not message.strip():
            raise ValueError("Message cannot be empty.")
        normalized_source = source.strip()
        if not normalized_source:
            raise ValueError("Message source cannot be empty.")
        session_name = self._require_session_name()
        if self._worker is None or self._worker.done():
            raise RuntimeError("Run the agent before sending messages.")
        async with self._state_lock:
            if self._state is AgentState.STOPPED:
                raise RuntimeError("The agent is stopped.")
            self._state = AgentState.GENERATING
            await self._event_pipeline.publish(
                MessageSent(
                    agent_name=self.name,
                    session_name=session_name,
                    message=message,
                    source=normalized_source,
                )
            )
            await self._messages.put(message)

    async def wait_for_message(self, timeout: float | None = None) -> str | None:
        """Return the completed response text, or ``None`` when no text is available.

        A turn interrupted by the provider resolves promptly with ``None``. This
        matches the existing timeout result and gives callers one stable outcome
        for a turn that produced no final assistant message.
        """
        try:
            if timeout is None:
                result = await self._completed_messages.get()
            else:
                result = await asyncio.wait_for(self._completed_messages.get(), timeout)
            if isinstance(result, Exception):
                raise result
            return result
        except asyncio.TimeoutError:
            return None

    async def cancel_generation(self) -> bool:
        async with self._state_lock:
            if self._state is not AgentState.GENERATING:
                return False
            name = self._require_session_name()
            self._cancel_requested = True
            current = self._sessions[name]
        updated = await self._client.interrupt(current)
        await self._replace_session(name, updated)
        return True

    async def close(self) -> None:
        async with self._state_lock:
            if self._state is AgentState.STOPPED:
                return
            self._state = AgentState.STOPPED
        await self._messages.put(None)
        if self._worker is not None:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
        await asyncio.gather(
            *(self._client.close(session) for session in self._sessions.values())
        )

    async def _process_messages(self) -> None:
        while True:
            message = await self._messages.get()
            if message is None:
                return
            try:
                await self._run_turn(message)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                async with self._state_lock:
                    if self._state is not AgentState.STOPPED:
                        self._state = AgentState.IDLE
                await self._event_pipeline.publish(
                    AgentError(
                        agent_name=self.name,
                        session_name=self._require_session_name(),
                        message=str(error),
                    )
                )
                await self._completed_messages.put(error)

    async def _run_turn(self, message: str) -> None:
        name = self._require_session_name()
        terminal_received = False
        try:
            updated = await self._client.start(self._sessions[name], message)
            await self._replace_session(name, updated)
            if self._cancel_requested:
                updated = await self._client.interrupt(self._sessions[name])
                await self._replace_session(name, updated)

            async for notification in self._client.notifications(
                self._sessions[name]
            ):
                await self._replace_session(name, notification.session)
                await self._event_pipeline.publish(
                    AgentEvent(
                        agent_name=self.name,
                        session_name=name,
                        payload=notification.event,
                    )
                )
                await self._steer_pending_messages(name)
                if isinstance(notification.event, ClientResponseCompleted):
                    response = notification.event.message
                    self._last_message = response
                    await self._completed_messages.put(response)
                    await self._event_pipeline.publish(
                        MessageSent(
                            agent_name=self.name,
                            session_name=name,
                            message=response,
                            source=self.name,
                        )
                    )
                    terminal_received = True
                if isinstance(notification.event, ClientResponseInterrupted):
                    # An interrupted response has no final text, but callers
                    # must still be released from wait_for_message().
                    await self._completed_messages.put(None)
                    terminal_received = True
            if not terminal_received:
                raise RuntimeError("The client notification stream ended before completion.")
        finally:
            async with self._state_lock:
                self._cancel_requested = False
                if self._state is not AgentState.STOPPED:
                    self._state = (
                        AgentState.GENERATING
                        if not self._messages.empty()
                        else AgentState.IDLE
                    )

    async def _steer_pending_messages(self, session_name: str) -> None:
        while True:
            try:
                message = self._messages.get_nowait()
            except asyncio.QueueEmpty:
                return
            if message is None:
                await self._messages.put(None)
                return
            updated = await self._client.steer(
                self._sessions[session_name],
                message,
            )
            await self._replace_session(session_name, updated)

    async def _replace_session(
        self,
        session_name: str,
        identifier: SessionIdentifier,
    ) -> None:
        current = self._sessions[session_name]
        if current.model_dump(mode="json") == identifier.model_dump(mode="json"):
            return
        self._sessions[session_name] = identifier
        await self._publish_session_update(session_name, identifier)

    async def _publish_session_update(
        self,
        session_name: str,
        identifier: SessionIdentifier,
    ) -> None:
        await self._event_pipeline.publish(
            SessionUpdated(
                agent_name=self.name,
                session_name=session_name,
                identifier=identifier.model_dump(mode="json"),
            )
        )

    def _require_idle(self) -> None:
        if self._state is AgentState.GENERATING:
            raise RuntimeError("Cannot switch sessions while the agent is generating.")
        if self._state is AgentState.STOPPED:
            raise RuntimeError("The agent is stopped.")

    def _require_session_name(self) -> str:
        if self._session_name is None:
            raise RuntimeError(f"Agent '{self.name}' does not have an active session.")
        return self._session_name

    @staticmethod
    def _validated_session_name(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Session name cannot be empty.")
        return normalized


__all__ = ["Agent", "AgentState", "ConfiguredMCPServer"]
