"""Focused unit coverage for the live-only inline approval state."""

from pairo.core.events import PermissionRequest
from pairo.core.permissions import PermissionRequestType
from pairo.ui.presentation import permission_request_history_content
from pairo.ui.models import EventDetailLevel, TranscriptEntry
from pairo.ui.widgets import AgentChatPane, PermissionRequestCard


def _request(request_id: str = "request-1") -> PermissionRequest:
    return PermissionRequest(
        request_id=request_id,
        agent_name="reviewer",
        session_name="Conversation 1",
        request_type=PermissionRequestType.COMMAND_EXECUTION,
        request={"command": "echo " + "long-command " * 50},
    )


def test_permission_card_uses_readable_immutable_decision_labels() -> None:
    assert PermissionRequestCard._decision_label("accept") == "Approved once"
    assert PermissionRequestCard._decision_label("acceptForSession") == "Approved for session"
    assert PermissionRequestCard._decision_label("decline") == "Denied"


def test_permission_history_remains_readable_without_becoming_its_identity() -> None:
    request = _request()
    expected = permission_request_history_content(request)
    assert "long-command" in expected
    assert expected.startswith("Approval requested\nAllow this command to run?")


def test_pending_cards_are_keyed_by_session_and_request_identifier() -> None:
    first = _request("same-id")
    second = first.model_copy(update={"session_name": "Conversation 2"})
    assert (first.session_name, first.request_id) != (second.session_name, second.request_id)


def test_pending_cards_use_permission_metadata_and_not_rendered_content() -> None:
    first = _request("first")
    second = _request("second")
    pane = AgentChatPane.__new__(AgentChatPane)
    pane._permission_cards = {
        (first.session_name, first.request_id): PermissionRequestCard(first),
        (second.session_name, second.request_id): PermissionRequestCard(second),
    }
    pane._pending_permission_positions = {
        (first.session_name, second.request_id): 0,
        (second.session_name, first.request_id): 1,
    }

    assert AgentChatPane._pending_permission_key(
        first.session_name,
        TranscriptEntry(
            "permission",
            "Approval requested\nThe text is not used as identity.",
            EventDetailLevel.NORMAL,
            {"type": "permission_request", "request_id": first.request_id},
        ),
    ) == (first.session_name, first.request_id)

    planned = pane._pending_permission_cards(first.session_name)

    assert planned[0].request is second
    assert planned[1].request is first
