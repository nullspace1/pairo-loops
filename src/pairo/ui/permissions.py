"""Repository-routed permission requests for the Textual host."""

from __future__ import annotations

from uuid import uuid4

from pairo.core.events import (
    EventPipeline,
    PermissionRequest,
    PermissionResponse,
    Subscription,
)
from pairo.core.permissions import (
    ApprovalRequest,
    PermissionDecision,
    PermissionRequester,
)

from .session_repository import SessionRepository


class TextualPermissionRequester(PermissionRequester):
    """Resolve opaque identifiers through storage and await a UI decision."""

    def __init__(
        self,
        event_pipeline: EventPipeline,
        session_repository: SessionRepository,
    ) -> None:
        self._event_pipeline = event_pipeline
        self._session_repository = session_repository

    async def request(self, request: ApprovalRequest) -> PermissionDecision:
        """Ask the UI for a decision on its principal event loop."""
        return await self._request(request)

    async def _request(self, request: ApprovalRequest) -> PermissionDecision:
        session = await self._session_repository.session_for_identifier(
            request.session_identifier
        )
        request_id = str(uuid4())
        decision: PermissionDecision | None = None

        def capture_response(
            event: PermissionResponse,
            subscription: Subscription[PermissionResponse],
        ) -> None:
            nonlocal decision
            if (
                event.request_id == request_id
                and event.agent_id == session.agent_id
                and event.session_name == session.session_name
            ):
                decision = event.decision
                self._event_pipeline.unsubscribe(subscription)

        subscription = self._event_pipeline.subscribe(
            PermissionResponse,
            capture_response,
        )
        try:
            self._event_pipeline.push(
                PermissionRequest(
                    request_id=request_id,
                    agent_id=session.agent_id,
                    session_name=session.session_name,
                    request_type=request.request_type,
                    request=request.details,
                )
            )
            await subscription.wait_until_cancelled()
            if decision is None:
                raise RuntimeError(
                    "Permission response subscription was cancelled without a decision."
                )
            return decision
        finally:
            self._event_pipeline.unsubscribe(subscription)


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
