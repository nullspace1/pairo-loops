"""Transcribe permission requests and render their interactive cards."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import cast

from pydantic import TypeAdapter, ValidationError

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Static

from pairo.core.agent import JsonObject
from pairo.core.events import PermissionRequest
from pairo.core.permissions import (
    AdditionalPermissionsApprovalRequest,
    ApprovalRequest,
    CommandExecutionApprovalRequest,
    FileChangeApprovalRequest,
)

from ..models import (
    EventDetailLevel,
    PermissionRequestTranscriptMetadata,
    SystemTranscriptMetadata,
    TranscriptEntry,
)
from ..permissions import build_permission_response
from ..presentation import permission_request_history_content
from .routed_transcript_entry import RoutedTranscriptEntry
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


_APPROVAL_REQUEST_ADAPTER: TypeAdapter[ApprovalRequest] = TypeAdapter(ApprovalRequest)


DecisionCallback = Callable[[str, "PermissionRequestCard"], Awaitable[None]]
DecisionLabel = Callable[[str], str]


class PermissionDetailWidget(Widget):
    """Structured, provider-neutral approval details for a permission card."""

    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__(classes="permission-details")
        self._request = request

    def _field(self, label: str, value: str) -> "PermissionField":
        """Create one readable, independently wrapping approval field."""
        return PermissionField(label, value)


class PermissionField(Vertical):
    """A compact label/value row for one decision-essential field."""

    def __init__(self, label: str, value: str) -> None:
        super().__init__(classes="permission-field")
        self._label = label
        self._value = value

    def compose(self) -> ComposeResult:
        yield Static(self._label, classes="permission-field-label")
        yield Static(self._value, classes="permission-field-value")


class PermissionRows(Vertical):
    """A compact labeled list whose entries are safe permission names or paths."""

    def __init__(self, label: str, values: tuple[str, ...]) -> None:
        super().__init__(classes="permission-field")
        self._label = label
        self._values = values

    def compose(self) -> ComposeResult:
        yield Static(self._label, classes="permission-field-label")
        for value in self._values:
            yield Static(value, classes="permission-row")


class CommandExecutionPermissionDetails(PermissionDetailWidget):
    """Details for a command-execution approval."""

    def compose(self) -> ComposeResult:
        request = cast(CommandExecutionApprovalRequest, self._request)
        yield Static("Command execution approval", classes="permission-heading")
        yield self._field("Command", request.command or "Not provided")
        yield self._field("Working directory", request.working_directory or "Not provided")
        yield self._field("Rationale", request.rationale or "Not provided")


class FileChangePermissionDetails(PermissionDetailWidget):
    """Details for a file-change approval."""

    def compose(self) -> ComposeResult:
        request = cast(FileChangeApprovalRequest, self._request)
        yield Static("File change approval", classes="permission-heading")
        yield PermissionRows("Affected files", request.affected_paths or ("Not provided",))
        yield self._field("Change summary", request.change_summary or "Not provided")
        yield self._field("Rationale", request.rationale or "Not provided")


class AdditionalPermissionsPermissionDetails(PermissionDetailWidget):
    """Details for an additional-permissions approval."""

    def compose(self) -> ComposeResult:
        request = cast(AdditionalPermissionsApprovalRequest, self._request)
        yield Static("Additional permissions approval", classes="permission-heading")
        yield PermissionRows(
            "Requested permissions",
            tuple(request.requested_permissions) or ("Not provided",),
        )
        yield self._field("Rationale", request.rationale or "Not provided")


class ExpiredPermissionDetails(Widget):
    """Readable durable details for non-actionable historical approvals."""

    def __init__(self, content: str) -> None:
        super().__init__(classes="permission-details")
        self._content = content

    def compose(self) -> ComposeResult:
        yield Static(self._content)


class _PermissionDetailFactory:
    def create(self, request: ApprovalRequest) -> PermissionDetailWidget:
        raise NotImplementedError


class _CommandExecutionDetailFactory(_PermissionDetailFactory):
    def create(self, request: ApprovalRequest) -> PermissionDetailWidget:
        return CommandExecutionPermissionDetails(request)


class _FileChangeDetailFactory(_PermissionDetailFactory):
    def create(self, request: ApprovalRequest) -> PermissionDetailWidget:
        return FileChangePermissionDetails(request)


class _AdditionalPermissionsDetailFactory(_PermissionDetailFactory):
    def create(self, request: ApprovalRequest) -> PermissionDetailWidget:
        return AdditionalPermissionsPermissionDetails(request)


_DETAIL_FACTORIES: dict[str, _PermissionDetailFactory] = {
    "command execution": _CommandExecutionDetailFactory(),
    "file change": _FileChangeDetailFactory(),
    "additional permissions": _AdditionalPermissionsDetailFactory(),
}


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
        if self._request is not None:
            request = self._request.request
            yield _DETAIL_FACTORIES[request.request_type.value].create(request)
        else:
            yield ExpiredPermissionDetails(
                self.entry.content.removeprefix("Approval requested\n")
            )
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
                    "request": cast(JsonObject, event.request.model_dump(mode="json")),
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
            context.event_pipeline.push(
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
            await context.transcript_service.update(
                context.agent_id,
                context.session_name,
                resolved,
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
        request_details = metadata.get("request")
        if (
            not isinstance(request_id, str)
            or not isinstance(request_details, dict)
        ):
            return None
        try:
            request = _APPROVAL_REQUEST_ADAPTER.validate_python(request_details)
        except ValidationError:
            return None

        return PermissionRequest(
            request_id=request_id,
            agent_id=context.agent_id,
            session_name=context.session_name,
            request=request,
        )

    async def _publish_error(
        self,
        context: TranscriptRenderContext,
        request_id: str,
        error: Exception,
    ) -> None:
        try:
            await context.transcript_service.add(
                context.agent_id,
                context.session_name,
                TranscriptEntry[SystemTranscriptMetadata](
                    "system",
                    f"Unable to persist permission {request_id}: {error}",
                    EventDetailLevel.NORMAL,
                    {"type": "system"},
                ),
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

