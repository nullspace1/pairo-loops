"""Focused coverage for permission transcript rendering state."""

from uuid import uuid4

from pairo.core.events import PermissionRequest
from pairo.core.permissions import PermissionRequestType
from pairo.ui.presentation import permission_request_history_content
from pairo.ui.transcribers import PermissionRequestTranscriber, default_transcribers


AGENT_ID = uuid4()


def _request(request_id: str = "request-1") -> PermissionRequest:
    return PermissionRequest(
        request_id=request_id,
        agent_id=AGENT_ID,
        session_name="Conversation 1",
        request_type=PermissionRequestType.COMMAND_EXECUTION,
        request={"command": "echo " + "long-command " * 50},
    )


def test_permission_card_uses_readable_immutable_decision_labels() -> None:
    assert PermissionRequestTranscriber.decision_label("accept") == "Approved once"
    assert PermissionRequestTranscriber.decision_label("acceptForSession") == "Approved for session"
    assert PermissionRequestTranscriber.decision_label("decline") == "Denied"


def test_permission_history_remains_readable_without_becoming_its_identity() -> None:
    request = _request()
    expected = permission_request_history_content(request)
    assert "long-command" in expected
    assert expected.startswith("Approval requested\nAllow this command to run?")


def test_permission_writer_persists_structured_pending_state() -> None:
    request = _request()
    routed = PermissionRequestTranscriber().write(request)

    assert routed is not None
    assert routed.entry.metadata == {
        "type": "permission_request",
        "request_id": request.request_id,
        "request_type": request.request_type.value,
        "request": request.request,
        "status": "pending",
    }


def test_default_transcribers_include_permission_request_rendering() -> None:
    registry = default_transcribers()

    assert registry.transcript_transcriber_for("permission_request") is not None
