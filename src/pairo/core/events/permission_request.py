"""Provider-neutral approval request event."""

from pydantic import BaseModel, ConfigDict
from uuid import UUID

from ..permissions import ApprovalRequest


class PermissionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    agent_id: UUID
    session_name: str
    request: ApprovalRequest

