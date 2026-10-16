"""Focused tests for the composed presentation formatters."""

from uuid import uuid4

from pairo.core.events import PermissionRequest
from pairo.core.permissions import PermissionRequestType
from pairo.ui.presentation import (
    PermissionHistoryPresenter,
    PresentationFormatter,
    ScalarPresenter,
)


def test_scalar_presenter_formats_boolean_missing_and_text_values() -> None:
    presenter = ScalarPresenter()
    formatter = PresentationFormatter()

    assert presenter.present(True, formatter).lines == ("Yes",)
    assert presenter.present(None, formatter).lines == ("Not specified",)
    assert presenter.present("  ready  ", formatter).lines == ("ready",)


def test_sequence_presenter_formats_nested_values_as_bullets() -> None:
    formatter = PresentationFormatter()

    rendered = formatter.present_value(["first", {"path": "pairo/ui/presentation.py"}, ["nested"]])

    assert rendered.lines == (
        "- first",
        "- Path: pairo/ui/presentation.py",
        "- Items:",
        "  - nested",
    )


def test_structured_presenter_hides_metadata_and_decodes_json_containers() -> None:
    formatter = PresentationFormatter()

    lines = formatter.describe_fields(
        {
            "threadId": "internal",
            "details": '{"enabled": true, "paths": ["pairo/ui/presentation.py"]}',
        }
    )

    assert lines == (
        "Details:",
        "  Enabled: Yes",
        "  Paths:",
        "    - pairo/ui/presentation.py",
    )


def test_structured_presenter_omits_empty_fields() -> None:
    assert PresentationFormatter().describe_fields({"reason": "", "paths": []}) == ()


def test_permission_history_presenter_wraps_the_human_approval_request() -> None:
    request = PermissionRequest(
        request_id="approval-1",
        agent_id=uuid4(),
        session_name="Session 1",
        request_type=PermissionRequestType.COMMAND_EXECUTION,
        request={"command": "pytest"},
    )

    assert PermissionHistoryPresenter().present(request) == "\n".join(
        ("Approval requested", "Allow this command to run?", "Command:", "pytest")
    )
