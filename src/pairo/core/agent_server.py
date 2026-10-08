"""Provider boundary consumed by the generic agent runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from .agent_config import AgentConfiguration, JsonObject
from .events.agent_events import ClientEvent


class SessionIdentifier(BaseModel):
    """Opaque provider state; generic code may only serialize it."""

    model_config = ConfigDict(extra="allow", frozen=True)


class SessionNotification(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    session: SessionIdentifier
    event: ClientEvent
    
MCP_AGENT_NAME_HEADER = "X-Agent-NAME"


class AgentClient(Protocol):
    async def new_session(
        self,
        configuration: AgentConfiguration,
        agent_name: str,
    ) -> SessionIdentifier: ...

    async def restore_session(
        self, configuration: AgentConfiguration, identifier_json: JsonObject
    ) -> SessionIdentifier: ...

    async def start(self, session: SessionIdentifier, message: str) -> SessionIdentifier: ...

    async def interrupt(self, session: SessionIdentifier) -> SessionIdentifier: ...

    async def steer(self, session: SessionIdentifier, message: str) -> SessionIdentifier: ...

    def notifications(
        self, session: SessionIdentifier
    ) -> AsyncIterator[SessionNotification]: ...

    async def close(self, session: SessionIdentifier) -> None: ...


__all__ = ["AgentClient", "SessionIdentifier", "SessionNotification"]
