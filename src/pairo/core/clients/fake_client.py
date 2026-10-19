"""Deterministic in-memory implementation of the asynchronous Agent protocol."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from secrets import token_urlsafe
from typing import Literal
from uuid import uuid4

from pydantic import ConfigDict

from pairo.core.agent.agent import AgentConfiguration
from pairo.core.agent.json_object import JsonObject
from pairo.core.agent.session_identifier import SessionIdentifier
from pairo.core.events import (
    AgentError,
    AgentResponseCompleted,
    AgentResponseInterrupted,
    EventPipeline,
    MessageSent,
    SessionCompacted,
    SessionCreated,
    SessionUpdated,
)

from .client import ActiveSession, AgentStatus


class FakeSessionIdentifier(SessionIdentifier):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["fake"] = "fake"
    session_id: str


class FakeAgent:
    """Produce one asynchronously delivered response for each fake turn."""

    def __init__(
        self,
        configuration: AgentConfiguration,
        event_pipeline: EventPipeline,
        response_factory: Callable[[], str] | None = None,
    ) -> None:
        self._configuration = configuration
        self._pipeline = event_pipeline
        self._response_factory = response_factory or token_urlsafe
        self._active_session: ActiveSession[FakeSessionIdentifier] | None = None
        self._status = AgentStatus.INACTIVE
        self._lock = asyncio.Lock()
        self._completion_task: asyncio.Task[None] | None = None
        self._pending_response: str | None = None
        self._interrupt_requested = False
        self._closed = False

    def configuration(self) -> AgentConfiguration:
        return self._configuration

    def current_session(self) -> ActiveSession[FakeSessionIdentifier] | None:
        return self._active_session

    def status(self) -> AgentStatus:
        return self._status

    async def new_session(self, name: str) -> ActiveSession[FakeSessionIdentifier]:
        normalized = self._validated_session_name(name)
        async with self._lock:
            self._require_open()
            self._require_not_generating()
            active = ActiveSession[FakeSessionIdentifier](
                name=normalized,
                identifier=FakeSessionIdentifier(session_id=str(uuid4())),
            )
            self._active_session = active
            self._status = AgentStatus.IDLE
        await self._pipeline.enqueue(
            SessionCreated(
                agent_id=self._configuration.id,
                session_name=active.name,
                identifier=active.identifier.model_dump(mode="json"),
            )
        )
        return active

    async def activate_session(
        self,
        name: str,
        identifier_json: JsonObject,
    ) -> ActiveSession[FakeSessionIdentifier]:
        normalized = self._validated_session_name(name)
        identifier = FakeSessionIdentifier.model_validate(identifier_json)
        async with self._lock:
            self._require_open()
            self._require_not_generating()
            active = ActiveSession[FakeSessionIdentifier](
                name=normalized,
                identifier=identifier,
            )
            self._active_session = active
            self._status = AgentStatus.IDLE
        await self._pipeline.enqueue(
            SessionUpdated(
                agent_id=self._configuration.id,
                session_name=active.name,
                identifier=active.identifier.model_dump(mode="json"),
            )
        )
        return active

    async def send_message(self, message: str, source: str = "user") -> None:
        normalized_message = self._validated_message(message)
        normalized_source = self._validated_source(source)
        async with self._lock:
            self._require_open()
            active = self._require_session()
            starting = self._status is not AgentStatus.GENERATING
            try:
                self._pending_response = self._response_factory()
            except Exception as error:
                await self._publish_error(active, error)
                raise
            self._status = AgentStatus.GENERATING
            if starting:
                self._interrupt_requested = False
        await self._pipeline.enqueue(
            MessageSent(
                agent_id=self._configuration.id,
                session_name=active.name,
                message=normalized_message,
                source=normalized_source,
            )
        )
        if starting:
            self._completion_task = self._pipeline.create_task(
                self._complete_turn(active),
                name=f"fake-agent-{self._configuration.id}",
            )

    async def interrupt(self) -> bool:
        async with self._lock:
            self._require_open()
            if self._status is not AgentStatus.GENERATING:
                return False
            self._interrupt_requested = True
            return True

    async def compact(self) -> None:
        async with self._lock:
            self._require_open()
            self._require_not_generating()
            active = self._require_session()
        await self._pipeline.enqueue(
            SessionCompacted(
                agent_id=self._configuration.id,
                session_name=active.name,
            )
        )

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            task = self._completion_task
            self._completion_task = None
            self._active_session = None
            self._pending_response = None
            self._status = AgentStatus.INACTIVE
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _complete_turn(
        self,
        active: ActiveSession[FakeSessionIdentifier],
    ) -> None:
        await asyncio.sleep(0)
        async with self._lock:
            if self._closed or self._active_session != active:
                return
            interrupted = self._interrupt_requested
            response = self._pending_response
            self._pending_response = None
            self._interrupt_requested = False
        try:
            if interrupted:
                await self._pipeline.enqueue(
                    AgentResponseInterrupted(
                        agent_id=self._configuration.id,
                        session_name=active.name,
                    )
                )
                return
            if response is None:
                error = RuntimeError("The fake turn has no pending response.")
                await self._publish_error(active, error)
                return
            await self._pipeline.enqueue(
                AgentResponseCompleted(
                    agent_id=self._configuration.id,
                    session_name=active.name,
                    message=response,
                )
            )
            await self._pipeline.enqueue(
                MessageSent(
                    agent_id=self._configuration.id,
                    session_name=active.name,
                    message=response,
                    source=self._configuration.name,
                )
            )
        finally:
            async with self._lock:
                if not self._closed and self._active_session == active:
                    self._status = AgentStatus.IDLE
                    self._completion_task = None

    async def _publish_error(
        self,
        active: ActiveSession[FakeSessionIdentifier],
        error: Exception,
    ) -> None:
        await self._pipeline.enqueue(
            AgentError(
                agent_id=self._configuration.id,
                session_name=active.name,
                message=str(error),
            )
        )

    def _require_session(self) -> ActiveSession[FakeSessionIdentifier]:
        if self._active_session is None:
            raise RuntimeError("The agent does not have an active session.")
        return self._active_session

    def _require_not_generating(self) -> None:
        if self._status is AgentStatus.GENERATING:
            raise RuntimeError("Cannot change sessions while the agent is generating.")

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("The agent is closed.")

    @classmethod
    def _validated_session_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Session name cannot be empty.")
        return normalized

    @classmethod
    def _validated_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be empty.")
        return value

    @classmethod
    def _validated_source(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Message source cannot be empty.")
        return normalized


__all__ = ["FakeAgent", "FakeSessionIdentifier"]
