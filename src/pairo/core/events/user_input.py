"""User input event."""

from pydantic import BaseModel


class UserInput(BaseModel):
    """New user text that should steer the active Codex turn."""

    message: str

