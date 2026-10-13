"""Provider-neutral approval response event."""

from pydantic import BaseModel, ConfigDict
from uuid import UUID

from ..permissions import PermissionDecision


class PermissionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    agent_id: UUID
    session_name: str
    decision: PermissionDecision
