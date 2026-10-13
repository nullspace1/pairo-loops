"""Conversation lifecycle coordinator for configured agents."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from contextlib import suppress
from types import MappingProxyType
from uuid import UUID

from pairo.core.agent.agent import Agent
from pairo.core.agent.agent_state import AgentState
from pairo.core.agent.json_object import JsonObject
from pairo.core.agent.run import AgentRun
from pairo.core.agent.session_identifier import SessionIdentifier
from pairo.core.clients.client import AgentClient
from pairo.core.events.agent_events import (
    AgentError,
    AgentEvent,
    ClientResponseCompleted,
    ClientResponseInterrupted,
    SessionUpdated,
)
from pairo.core.events.event_pipeline import EventPipeline
from pairo.core.events.message_sent import MessageSent


class Runner:
    """Own all conversation state and provider operations for a set of agents."""

    def __init__(
        self,
        agents: Sequence[Agent],
        pipeline: EventPipeline,
        client: AgentClient,
    ) -> None:
        configured_agents = {agent.id: agent for agent in agents}
        if not configured_agents:
            raise ValueError("Runner requires at least one agent.")
        if len(configured_agents) != len(agents):
            raise ValueError("Agent IDs must be unique.")
        self._agents = configured_agents
        self._runs = {agent_id: AgentRun() for agent_id in configured_agents}
        self._pipeline = pipeline
        self._client = client
        self._started = False
        self._closed = False

    @property
    def agents(self) -> Mapping[UUID, Agent]:
        return MappingProxyType(self._agents)

    @property
    def pipeline(self) -> EventPipeline:
        return self._pipeline

    async def start(self, initial_session_name: str = "Conversation 1") -> None:
        self._require_open()
        if self._started:
            raise RuntimeError("The runner is already started.")
        session_name = self._validated_session_name(initial_session_name)
        self._started = True
        await asyncio.gather(
            *(
                self.create_session(agent_id, session_name)
                for agent_id in self._agents
            )
        )

    async def create_session(self, agent_id: UUID, session_name: str) -> str:
        agent, run = self._agent_and_run(agent_id)
        name = self._validated_session_name(session_name)
        run.require_idle()
        if name in run.sessions:
            raise ValueError(f"Session '{name}' already exists for agent '{agent.id}'.")
        identifier = await self._client.new_session(agent)
        await run.set_session(name, identifier)
        self._ensure_worker(agent, run)
        await self._publish_session_update(agent, name, identifier)
        return name

    async def restore_session(
        self,
        agent_id: UUID,
        session_name: str,
        identifier_json: JsonObject,
    ) -> str:
        agent, run = self._agent_and_run(agent_id)
        name = self._validated_session_name(session_name)
        run.require_idle()
        if name in run.sessions:
            raise ValueError(f"Session '{name}' is already loaded for agent '{agent.id}'.")
        identifier = await self._client.restore_session(agent, identifier_json)
        await run.set_session(name, identifier)
        self._ensure_worker(agent, run)
        await self._publish_session_update(agent, name, identifier)
        return name

    async def switch_session(self, agent_id: UUID, session_name: str) -> str:
        _, run = self._agent_and_run(agent_id)
        name = self._validated_session_name(session_name)
        await run.select_session(name)
        return name

    async def send_message(
        self,
        agent_id: UUID,
        message: str,
        source: str = "user",
    ) -> None:
        agent, run = self._agent_and_run(agent_id)
        if not message.strip():
            raise ValueError("Message cannot be empty.")
        normalized_source = source.strip()
        if not normalized_source:
            raise ValueError("Message source cannot be empty.")
        session_name = run.require_session_name()
        if run.worker is None or run.worker.done():
            raise RuntimeError("Start a session before sending messages.")
        await self._pipeline.publish(
            MessageSent(
                agent_id=agent.id,
                session_name=session_name,
                message=message,
                source=normalized_source,
            )
        )
        await run.enqueue(message)

    async def wait_for_message(
        self,
        agent_id: UUID,
        timeout: float | None = None,
    ) -> str | None:
        _, run = self._agent_and_run(agent_id)
        return await run.wait_for_response(timeout)

    async def interrupt(self, agent_id: UUID) -> bool:
        agent, run = self._agent_and_run(agent_id)
        requested, identifier = await run.request_interrupt()
        if identifier is not None:
            updated = await self._client.interrupt(identifier)
            await self._update_session(agent, run, updated)
        return requested

    def session_name(self, agent_id: UUID) -> str | None:
        return self._run(agent_id).session_name

    def session_names(self, agent_id: UUID) -> tuple[str, ...]:
        return self._run(agent_id).session_names

    def state(self, agent_id: UUID) -> AgentState:
        return self._run(agent_id).state

    def is_generating(self, agent_id: UUID) -> bool:
        return self._run(agent_id).is_generating

    def last_message(self, agent_id: UUID) -> str | None:
        return self._run(agent_id).last_message

    def session_identifier_json(
        self,
        agent_id: UUID,
        session_name: str,
    ) -> JsonObject:
        return self._run(agent_id).session_identifier_json(
            self._validated_session_name(session_name)
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await asyncio.gather(*(run.stop() for run in self._runs.values()))
        workers = [run.worker for run in self._runs.values() if run.worker is not None]
        for worker in workers:
            worker.cancel()
        for worker in workers:
            with suppress(asyncio.CancelledError):
                await worker
        await self._client.close()

    def _ensure_worker(self, agent: Agent, run: AgentRun) -> None:
        if run.worker is None:
            run.worker = asyncio.create_task(
                self._process_messages(agent, run),
                name=f"agent-{agent.id}",
            )

    async def _process_messages(self, agent: Agent, run: AgentRun) -> None:
        while True:
            message = await run.next_request()
            if message is None:
                return
            try:
                await self._run_turn(message, agent, run)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await run.add_response(error)
                await self._pipeline.publish(
                    AgentError(
                        agent_id=agent.id,
                        session_name=run.require_session_name(),
                        message=str(error),
                    )
                )
            finally:
                await run.finish_turn()

    async def _run_turn(self, message: str, agent: Agent, run: AgentRun) -> None:
        updated = await self._client.start(run.session_identifier(), message)
        await self._update_session(agent, run, updated)
        run.turn_started.set()
        if await run.claim_pending_interrupt():
            interrupted = await self._client.interrupt(run.session_identifier())
            await self._update_session(agent, run, interrupted)

        terminal_received = False
        async for notification in self._client.notifications(run.session_identifier()):
            await self._update_session(agent, run, notification.session)
            session_name = run.require_session_name()
            await self._pipeline.publish(
                AgentEvent(
                    agent_id=agent.id,
                    session_name=session_name,
                    payload=notification.event,
                )
            )
            await self._steer_pending_messages(agent, run)
            if isinstance(notification.event, ClientResponseCompleted):
                response = notification.event.message
                await run.add_response(response)
                await self._pipeline.publish(
                    MessageSent(
                        agent_id=agent.id,
                        session_name=session_name,
                        message=response,
                        source=agent.name,
                    )
                )
                terminal_received = True
            elif isinstance(notification.event, ClientResponseInterrupted):
                await run.add_response(None)
                terminal_received = True
        if not terminal_received:
            raise RuntimeError("The client notification stream ended before completion.")

    async def _steer_pending_messages(self, agent: Agent, run: AgentRun) -> None:
        while True:
            try:
                message = run.next_request_nowait()
            except asyncio.QueueEmpty:
                return
            if message is None:
                await run.requests.put(None)
                return
            updated = await self._client.steer(run.session_identifier(), message)
            await self._update_session(agent, run, updated)

    async def _update_session(
        self,
        agent: Agent,
        run: AgentRun,
        identifier: SessionIdentifier,
    ) -> None:
        if await run.replace_identifier(identifier):
            await self._publish_session_update(
                agent,
                run.require_session_name(),
                identifier,
            )

    async def _publish_session_update(
        self,
        agent: Agent,
        session_name: str,
        identifier: SessionIdentifier,
    ) -> None:
        await self._pipeline.publish(
            SessionUpdated(
                agent_id=agent.id,
                session_name=session_name,
                identifier=identifier.model_dump(mode="json"),
            )
        )

    def _agent_and_run(self, agent_id: UUID) -> tuple[Agent, AgentRun]:
        self._require_open()
        try:
            return self._agents[agent_id], self._runs[agent_id]
        except KeyError as error:
            raise KeyError(f"Agent '{agent_id}' is not configured.") from error

    def _run(self, agent_id: UUID) -> AgentRun:
        try:
            return self._runs[agent_id]
        except KeyError as error:
            raise KeyError(f"Agent '{agent_id}' is not configured.") from error

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("The runner is closed.")

    @staticmethod
    def _validated_session_name(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Session name cannot be empty.")
        return normalized


__all__ = ["Runner"]
