"""Repository-routed permission requests for the Textual host."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4
from uuid import UUID

from pydantic import BaseModel

from pairo.core.events import EventPipeline, PermissionRequest, PermissionResponse
from pairo.core.permissions import (
    ApprovalRequest,
    PermissionDecision,
    PermissionRequester,
)

from .session_repository import SessionRepository


@dataclass(frozen=True, slots=True)
class _PendingPermissionResponse:
    """Bind an approval future to the session that requested it."""

    agent_id: UUID
    session_name: str
    response: asyncio.Future[PermissionDecision]


class TextualPermissionRequester(PermissionRequester):
    """Resolve opaque identifiers through storage and await a UI decision."""

    def __init__(
        self,
        event_pipeline: EventPipeline,
        session_repository: SessionRepository,
    ) -> None:
        self._event_pipeline = event_pipeline
        self._session_repository = session_repository
        self._responses: dict[str, _PendingPermissionResponse] = {}
        self._event_pipeline.add_listener(PermissionResponse, self._capture_response)

    async def request(self, request: ApprovalRequest) -> PermissionDecision:
        session = await self._session_repository.session_for_identifier(
            request.session_identifier
        )
        request_id = str(uuid4())
        response = asyncio.get_running_loop().create_future()
        self._responses[request_id] = _PendingPermissionResponse(
            agent_id=session.agent_id,
            session_name=session.session_name,
            response=response,
        )
        try:
            await self._event_pipeline.publish(
                PermissionRequest(
                    request_id=request_id,
                    agent_id=session.agent_id,
                    session_name=session.session_name,
                    request_type=request.request_type,
                    request=request.details,
                )
            )
            return await response
        finally:
            self._responses.pop(request_id, None)

    def _capture_response(self, event: BaseModel) -> BaseModel:
        if isinstance(event, PermissionResponse):
            pending = self._responses.get(event.request_id)
            if (
                pending is not None
                and pending.agent_id == event.agent_id
                and pending.session_name == event.session_name
                and not pending.response.done()
            ):
                pending.response.set_result(event.decision)
        return event


def build_permission_response(
    request: PermissionRequest,
    decision: str,
) -> PermissionResponse:
    try:
        normalized = PermissionDecision(decision)
    except ValueError as error:
        raise ValueError(f"Unknown permission decision: {decision}") from error
    return PermissionResponse(
        request_id=request.request_id,
        agent_id=request.agent_id,
        session_name=request.session_name,
        decision=normalized,
    )
