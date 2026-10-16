"""Mutable state for one agent managed by a :class:`Runner`."""

from __future__ import annotations

import asyncio

from pairo.core.agent.agent_state import AgentState
from pairo.core.agent.interrupt_state import InterruptState
from pairo.core.agent.json_object import JsonObject
from pairo.core.agent.session_identifier import SessionIdentifier


class AgentRun:
    """Own queues and named provider sessions for one configured agent."""

    def __init__(self) -> None:
        self.sessions: dict[str, SessionIdentifier] = {}
        self.session_name: str | None = None
        self.requests: asyncio.Queue[str | None] = asyncio.Queue()
        self.responses: asyncio.Queue[str | Exception | None] = asyncio.Queue()
        self.state_lock = asyncio.Lock()
        self.state = AgentState.IDLE
        self.interrupt_state = InterruptState.NONE
        self.turn_started = asyncio.Event()
        self.last_message: str | None = None
        self.worker: asyncio.Task[None] | None = None

    @property
    def session_names(self) -> tuple[str, ...]:
        return tuple(self.sessions)

    @property
    def is_generating(self) -> bool:
        return self.state is AgentState.GENERATING

    def session_identifier(self, session_name: str | None = None) -> SessionIdentifier:
        name = session_name or self.require_session_name()
        try:
            return self.sessions[name]
        except KeyError as error:
            raise KeyError(f"Session '{name}' is not loaded.") from error

    def session_identifier_json(self, session_name: str) -> JsonObject:
        return self.session_identifier(session_name).model_dump(mode="json")

    async def set_session(
        self,
        session_name: str,
        identifier: SessionIdentifier,
    ) -> None:
        async with self.state_lock:
            self.require_idle()
            self.sessions[session_name] = identifier
            self.session_name = session_name

    async def select_session(self, session_name: str) -> None:
        async with self.state_lock:
            self.require_idle()
            if session_name not in self.sessions:
                raise KeyError(f"Session '{session_name}' is not loaded.")
            self.session_name = session_name

    async def delete_session(self, session_name: str) -> str | None:
        """Forget one loaded session and select another loaded session if needed."""
        async with self.state_lock:
            self.require_idle()
            if session_name not in self.sessions:
                raise KeyError(f"Session '{session_name}' is not loaded.")
            del self.sessions[session_name]
            if self.session_name == session_name:
                self.session_name = next(reversed(self.sessions), None)
            return self.session_name

    async def enqueue(self, message: str) -> None:
        async with self.state_lock:
            if self.state is AgentState.STOPPED:
                raise RuntimeError("The agent run is stopped.")
            self.require_session_name()
            self.state = AgentState.GENERATING
            await self.requests.put(message)

    async def next_request(self) -> str | None:
        return await self.requests.get()

    def next_request_nowait(self) -> str | None:
        return self.requests.get_nowait()

    async def add_response(self, response: str | Exception | None) -> None:
        if isinstance(response, str):
            self.last_message = response
        await self.responses.put(response)

    async def wait_for_response(self, timeout: float | None) -> str | None:
        try:
            result = (
                await self.responses.get()
                if timeout is None
                else await asyncio.wait_for(self.responses.get(), timeout)
            )
        except asyncio.TimeoutError:
            return None
        if isinstance(result, Exception):
            raise result
        return result

    async def replace_identifier(self, identifier: SessionIdentifier) -> bool:
        async with self.state_lock:
            name = self.require_session_name()
            current = self.sessions[name]
            if current.model_dump(mode="json") == identifier.model_dump(mode="json"):
                return False
            self.sessions[name] = identifier
            return True

    async def finish_turn(self) -> None:
        async with self.state_lock:
            self.interrupt_state = InterruptState.NONE
            self.turn_started.clear()
            if self.state is not AgentState.STOPPED:
                self.state = (
                    AgentState.GENERATING
                    if not self.requests.empty()
                    else AgentState.IDLE
                )

    async def request_interrupt(self) -> tuple[bool, SessionIdentifier | None]:
        """Record an interrupt and claim an already-started turn if available."""
        async with self.state_lock:
            if self.state is not AgentState.GENERATING:
                return False, None
            if self.interrupt_state is InterruptState.NONE:
                self.interrupt_state = InterruptState.PENDING
            if (
                not self.turn_started.is_set()
                or self.interrupt_state is InterruptState.SENT
            ):
                return True, None
            self.interrupt_state = InterruptState.SENT
            return True, self.session_identifier()

    async def claim_pending_interrupt(self) -> bool:
        async with self.state_lock:
            if self.interrupt_state is not InterruptState.PENDING:
                return False
            self.interrupt_state = InterruptState.SENT
            return True

    async def stop(self) -> None:
        async with self.state_lock:
            if self.state is AgentState.STOPPED:
                return
            self.state = AgentState.STOPPED
        await self.requests.put(None)

    def require_idle(self) -> None:
        if self.state is AgentState.GENERATING:
            raise RuntimeError("Cannot switch sessions while the agent is generating.")
        if self.state is AgentState.STOPPED:
            raise RuntimeError("The agent run is stopped.")

    def require_session_name(self) -> str:
        if self.session_name is None:
            raise RuntimeError("The agent does not have an active session.")
        return self.session_name


__all__ = ["AgentRun"]

