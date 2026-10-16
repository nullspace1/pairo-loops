from pydantic import BaseModel, ConfigDict


class SessionIdentifier(BaseModel):
    """Opaque provider state; generic code may only serialize it."""

    model_config = ConfigDict(extra="allow", frozen=True)

