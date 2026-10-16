"""Provider boundary consumed by the generic agent runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from pairo.core.agent.agent import Agent
from pairo.core.agent.json_object import JsonObject
from pairo.core.agent.session_identifier import SessionIdentifier
from ..events.agent_events import ClientEvent




class SessionNotification(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    session: SessionIdentifier
    event: ClientEvent
    
MCP_AGENT_ID_HEADER = "X-Agent-ID"


class AgentClient(Protocol):
    async def new_session(
        self,
        agent: Agent,
    ) -> SessionIdentifier: ...

    async def restore_session(
        self, agent: Agent, identifier_json: JsonObject
    ) -> SessionIdentifier: ...

    async def start(self, session: SessionIdentifier, message: str) -> SessionIdentifier: ...

    async def interrupt(self, session: SessionIdentifier) -> SessionIdentifier: ...

    async def compact(self, session: SessionIdentifier) -> None: ...

    async def steer(self, session: SessionIdentifier, message: str) -> SessionIdentifier: ...

    def notifications(
        self, session: SessionIdentifier
    ) -> AsyncIterator[SessionNotification]: ...

    async def close(self) -> None: ...


__all__ = [
    "AgentClient",
    "MCP_AGENT_ID_HEADER",
    "SessionIdentifier",
    "SessionNotification",
]

