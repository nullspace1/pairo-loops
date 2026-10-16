"""Inter-agent message delivered through a communications channel."""

from pydantic import BaseModel, ConfigDict
from uuid import UUID


class NetworkMessageSent(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel_name: str
    sender_id: UUID
    sender_name: str
    recipient_id: UUID
    session_name: str
    message: str


