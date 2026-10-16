from pydantic import BaseModel, ConfigDict, Field, field_validator


class Hook(BaseModel):
    """A shell command executed by Codex for a configured lifecycle event."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    command: str
    is_async: bool = Field(alias="async")

    @field_validator("command")
    @classmethod
    def require_command(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Hook command cannot be empty.")
        return normalized

