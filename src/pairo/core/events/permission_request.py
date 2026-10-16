"""Provider-neutral approval request event."""

from pydantic import BaseModel, ConfigDict
from uuid import UUID

from ..agent.json_object import JsonObject
from ..permissions import PermissionRequestType


class PermissionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    agent_id: UUID
    session_name: str
    request_type: PermissionRequestType
    request: JsonObject

