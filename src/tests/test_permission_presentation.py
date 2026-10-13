"""Tests for approval prompts written for people rather than SDK payloads."""

import pytest
from uuid import uuid4

from pairo.core.events import PermissionRequest
from pairo.core.permissions import PermissionRequestType
from pairo.ui.presentation import PermissionRequestPresenter


AGENT_ID = uuid4()


def test_command_approval_prioritizes_command_location_and_reason() -> None:
    request = PermissionRequest(
        request_id="approval-1",
        agent_id=AGENT_ID,
        session_name="Session 1",
        request_type=PermissionRequestType.COMMAND_EXECUTION,
        request={
            "command": "pytest tests/test_ui_app.py",
            "cwd": "C:/project",
            "reason": "Verify the user-interface change.",
            "threadId": "internal-thread",
        },
    )

    prompt = PermissionRequestPresenter().present(request)

    assert prompt == "\n".join(
        (
            "Allow this command to run?",
            "Command:",
            "pytest tests/test_ui_app.py",
            "Working directory: C:/project",
            "Why this is needed: Verify the user-interface change.",
        )
    )


def test_command_approval_keeps_unexpected_user_relevant_details() -> None:
    request = PermissionRequest(
        request_id="approval-2",
        agent_id=AGENT_ID,
        session_name="Session 1",
        request_type=PermissionRequestType.COMMAND_EXECUTION,
        request={"command": "npm test", "timeoutMs": 30_000},
    )

    prompt = PermissionRequestPresenter().present(request)

    assert "Timeout ms: 30000" in prompt


def test_file_change_approval_prioritizes_files_changes_and_reason() -> None:
    request = PermissionRequest(
        request_id="approval-3",
        agent_id=AGENT_ID,
        session_name="Session 1",
        request_type=PermissionRequestType.FILE_CHANGE,
        request={
            "paths": ["pairo/ui/presentation.py", "tests/test_permission_presentation.py"],
            "changes": "Add clear file-change approval details.",
            "justification": "Make the requested approval decision informed.",
            "threadId": "internal-thread",
        },
    )

    prompt = PermissionRequestPresenter().present(request)

    assert prompt == "\n".join(
        (
            "Allow these file changes?",
            "Affected files:",
            "- pairo/ui/presentation.py",
            "- tests/test_permission_presentation.py",
            "Changes: Add clear file-change approval details.",
            "Why this is needed: Make the requested approval decision informed.",
        )
    )


@pytest.mark.parametrize("rationale_field", ["reason", "rationale"])
def test_file_change_approval_describes_a_single_path_and_rationale(
    rationale_field: str,
) -> None:
    request = PermissionRequest(
        request_id="approval-4",
        agent_id=AGENT_ID,
        session_name="Session 1",
        request_type=PermissionRequestType.FILE_CHANGE,
        request={"path": "pairo/ui/app.py", rationale_field: "Clarify the approval."},
    )

    prompt = PermissionRequestPresenter().present(request)

    assert "Affected file: pairo/ui/app.py" in prompt
    assert "Why this is needed: Clarify the approval." in prompt


def test_file_change_approval_formats_structured_changes_and_hides_identifiers() -> None:
    request = PermissionRequest(
        request_id="approval-5",
        agent_id=AGENT_ID,
        session_name="Session 1",
        request_type=PermissionRequestType.FILE_CHANGE,
        request={
            "changes": {
                "files": [
                    {
                        "path": "pairo/ui/widgets.py",
                        "summary": "Add a scrollbar.",
                        "threadId": "hidden-nested-id",
                    }
                ]
            },
            "requestId": "hidden-request-id",
            "turnId": "hidden-turn-id",
        },
    )

    prompt = PermissionRequestPresenter().present(request)

    assert "Changes:" in prompt
    assert "Files:" in prompt
    assert "Path: pairo/ui/widgets.py" in prompt
    assert "Summary: Add a scrollbar." in prompt
    assert "hidden-" not in prompt


def test_file_change_approval_explains_missing_optional_details() -> None:
    request = PermissionRequest(
        request_id="approval-6",
        agent_id=AGENT_ID,
        session_name="Session 1",
        request_type=PermissionRequestType.FILE_CHANGE,
        request={"threadId": "internal-thread"},
    )

    assert PermissionRequestPresenter().present(request) == "\n".join(
        ("Allow these file changes?", "The agent did not provide file-change details.")
    )
