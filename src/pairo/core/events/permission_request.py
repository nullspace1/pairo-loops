"""Provider-neutral approval request event."""

from pydantic import BaseModel, ConfigDict

from ..agent_config import JsonObject
from ..permissions import PermissionRequestType


class PermissionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    agent_name: str
    session_name: str
    request_type: PermissionRequestType
    request: JsonObject
