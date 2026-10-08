"""Completed agent text output."""

from pydantic import BaseModel, ConfigDict, field_validator


class MessageSent(BaseModel):
    """A chat message routed to an agent session, including its display source."""

    model_config = ConfigDict(frozen=True)

    agent_name: str
    session_name: str
    message: str
    source: str = "user"

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Message source cannot be empty.")
        return normalized
