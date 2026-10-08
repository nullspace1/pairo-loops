"""Inter-agent message delivered through a communications channel."""

from pydantic import BaseModel, ConfigDict


class NetworkMessageSent(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel_name: str
    sender_name: str
    recipient_name: str
    session_name: str
    message: str

