"""Transcribe permission requests and render their interactive cards."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import cast

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Static

from pairo.core.events import PermissionRequest
from pairo.core.permissions import PermissionRequestType

from ..models import (
    EventDetailLevel,
    PermissionRequestTranscriptMetadata,
    SystemTranscriptMetadata,
    TranscriptEntry,
)
from ..permissions import build_permission_response
from ..presentation import PermissionRequestPresenter, permission_request_history_content
from ..transcript_events import TranscriptEntryAdded, TranscriptEntryUpdated
from .routed_transcript_entry import RoutedTranscriptEntry
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


_REQUEST_TYPES_BY_VALUE = {
    request_type.value: request_type for request_type in PermissionRequestType
}


DecisionCallback = Callable[[str, "PermissionRequestCard"], Awaitable[None]]
DecisionLabel = Callable[[str], str]


class PermissionRequestCard(Vertical):
    """Interactive presentation owned by the permission renderer."""

    def __init__(
        self,
        entry: TranscriptEntry[PermissionRequestTranscriptMetadata],
        request: PermissionRequest | None,
        answer: DecisionCallback,
        decision_label: DecisionLabel,
        *,
        classes: str | None = None,
    ) -> None:
        super().__init__(classes=classes)
        self.entry = entry
        self._request = request
        self._answer = answer
        self._decision_label = decision_label
        self._decision = entry.metadata.get("decision")

    def compose(self) -> ComposeResult:
        yield Static(Text(self._details()), classes="permission-details")
        with Horizontal(classes="permission-actions"):
            yield Button("Approve once", name="accept", variant="success")
            yield Button("Approve session", name="acceptForSession", variant="primary")
            yield Button("Decline", name="decline", variant="error")
        yield Static("", classes="permission-decision")

    def on_mount(self) -> None:
        if self._decision is not None:
            self.resolve(str(self._decision))
        elif self._request is None:
            self._set_actions_disabled(True)
            self._set_status("Expired — this request is no longer actionable.")
        else:
            self.query_one(".permission-decision", Static).display = False

    @on(Button.Pressed)
    async def decide(self, event: Button.Pressed) -> None:
        decision = event.button.name
        if self._decision is None and decision is not None and self._request is not None:
            event.stop()
            self._set_actions_disabled(True)
            await self._answer(decision, self)

    def resolve(self, decision: str) -> None:
        self._decision = decision
        self._set_actions_disabled(True)
        self._set_status(f"Decision: {self._decision_label(decision)}")

    def allow_retry(self) -> None:
        if self._decision is None and self._request is not None:
            self._set_actions_disabled(False)

    def show_error(self, message: str) -> None:
        self._set_status(message)

    def _details(self) -> str:
        if self._request is not None:
            return PermissionRequestPresenter().present(self._request)
        return self.entry.content.removeprefix("Approval requested\n")

    def _set_actions_disabled(self, disabled: bool) -> None:
        for button in self.query(Button):
            button.disabled = disabled

    def _set_status(self, value: str) -> None:
        label = self.query_one(".permission-decision", Static)
        label.update(value)
        label.display = True

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()


class PermissionRequestTranscriber(
    Transcriber[PermissionRequest, PermissionRequestTranscriptMetadata]
):
    def __init__(self) -> None:
        super().__init__(PermissionRequest, "permission_request")

    def _write(
        self, event: PermissionRequest
    ) -> RoutedTranscriptEntry[PermissionRequestTranscriptMetadata]:
        return RoutedTranscriptEntry(
            event.agent_id,
            event.session_name,
            TranscriptEntry(
                "permission",
                permission_request_history_content(event),
                EventDetailLevel.NORMAL,
                {
                    "type": "permission_request",
                    "request_id": event.request_id,
                    "request_type": event.request_type.value,
                    "request": event.request,
                    "status": "pending",
                },
            ),
        )

    def render(
        self,
        transcript: TranscriptEntry[PermissionRequestTranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> Widget:
        request = self._live_request(transcript, context)

        async def answer(decision: str, card: PermissionRequestCard) -> None:
            await self._answer(transcript, context, request, decision, card)

        return PermissionRequestCard(
            transcript,
            request,
            answer,
            self.decision_label,
            classes="permission-card",
        )

    async def _answer(
        self,
        entry: TranscriptEntry[PermissionRequestTranscriptMetadata],
        context: TranscriptRenderContext,
        request: PermissionRequest | None,
        decision: str,
        card: PermissionRequestCard,
    ) -> None:
        request_id = str(entry.metadata.get("request_id", ""))
        if request is None:
            card.show_error("This approval request is no longer actionable.")
            return
        try:
            await context.event_pipeline.enqueue(
                build_permission_response(request, decision)
            )
        except (RuntimeError, ValueError) as error:
            card.allow_retry()
            card.show_error(f"Unable to answer permission: {error}")
            await self._publish_error(context, request_id, error)
            return

        card.resolve(decision)
        metadata = dict(entry.metadata)
        metadata.update(status="resolved", decision=decision)
        resolved = replace(
            entry,
            content=f"{entry.content}\n\nDecision: {self.decision_label(decision)}",
            metadata=cast(PermissionRequestTranscriptMetadata, metadata),
        )
        try:
            await context.event_pipeline.enqueue(
                TranscriptEntryUpdated(
                    agent_id=context.agent_id,
                    session_name=context.session_name,
                    entry=resolved,
                )
            )
        except (LookupError, RuntimeError, ValueError) as error:
            card.show_error(f"Decision sent, but history could not be updated: {error}")
            await self._publish_error(context, request_id, error)

    @staticmethod
    def _live_request(
        entry: TranscriptEntry[PermissionRequestTranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> PermissionRequest | None:
        metadata = entry.metadata
        if (
            not context.is_live
            or metadata.get("type") != "permission_request"
            or metadata.get("status") != "pending"
        ):
            return None

        request_id = metadata.get("request_id")
        request_type_value = metadata.get("request_type")
        request_details = metadata.get("request")
        if (
            not isinstance(request_id, str)
            or not isinstance(request_type_value, str)
            or not isinstance(request_details, dict)
        ):
            return None
        request_type = _REQUEST_TYPES_BY_VALUE.get(request_type_value)
        if request_type is None:
            return None

        return PermissionRequest(
            request_id=request_id,
            agent_id=context.agent_id,
            session_name=context.session_name,
            request_type=request_type,
            request=request_details,
        )

    async def _publish_error(
        self,
        context: TranscriptRenderContext,
        request_id: str,
        error: Exception,
    ) -> None:
        try:
            await context.event_pipeline.enqueue(
                TranscriptEntryAdded(
                    agent_id=context.agent_id,
                    session_name=context.session_name,
                    entry=TranscriptEntry[SystemTranscriptMetadata](
                        "system",
                        f"Unable to persist permission {request_id}: {error}",
                        EventDetailLevel.NORMAL,
                        {"type": "system"},
                    ),
                )
            )
        except Exception:
            # The card already presents the failure. Do not recursively emit
            # another transcript event if transcript persistence is unavailable.
            return

    @staticmethod
    def decision_label(decision: str) -> str:
        return {
            "accept": "Approved once",
            "acceptForSession": "Approved for session",
            "decline": "Denied",
        }.get(decision, decision)
