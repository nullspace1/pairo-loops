"""Provider-neutral asynchronous agent contract."""

from __future__ import annotations

from enum import Enum
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict

from pairo.core.agent.agent import AgentConfiguration
from pairo.core.agent.json_object import JsonObject
from pairo.core.agent.session_identifier import SessionIdentifier


MCP_AGENT_ID_HEADER = "X-Agent-ID"


class AgentStatus(str, Enum):
    INACTIVE = "inactive"
    IDLE = "idle"
    GENERATING = "generating"


IdentifierT = TypeVar("IdentifierT", bound=SessionIdentifier, covariant=True)


class ActiveSession(BaseModel, Generic[IdentifierT]):
    """The only application session retained by one agent."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str
    identifier: IdentifierT


class Agent(Protocol[IdentifierT]):
    def configuration(self) -> AgentConfiguration: ...

    def current_session(self) -> ActiveSession[IdentifierT] | None: ...

    def status(self) -> AgentStatus: ...

    async def new_session(self, name: str) -> ActiveSession[IdentifierT]: ...

    async def activate_session(
        self,
        name: str,
        identifier_json: JsonObject,
    ) -> ActiveSession[IdentifierT]: ...

    async def send_message(self, message: str, source: str = "user") -> None: ...

    async def interrupt(self) -> bool: ...

    async def compact(self) -> None: ...

    async def close(self) -> None: ...


__all__ = [
    "ActiveSession",
    "Agent",
    "AgentStatus",
    "MCP_AGENT_ID_HEADER",
]
