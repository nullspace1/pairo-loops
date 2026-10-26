"""UI-owned navigation events emitted by command handling."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AgentSwitchRequested(BaseModel):
    """Request that the UI display the configured agent's chat pane."""

    model_config = ConfigDict(frozen=True)

    agent_id: UUID


class SessionSwitchRequested(BaseModel):
    """Request that the UI display an active session in its agent pane."""

    model_config = ConfigDict(frozen=True)

    agent_id: UUID
    session_name: str


__all__ = ["AgentSwitchRequested", "SessionSwitchRequested"]
