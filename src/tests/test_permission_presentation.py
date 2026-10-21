"""Tests for decision-essential approval presentation."""

from uuid import uuid4

from pairo.core.events import PermissionRequest
from pairo.core.permissions import (
    AdditionalPermissionsApprovalRequest,
    CommandExecutionApprovalRequest,
    FileChangeApprovalRequest,
)
from pairo.ui.presentation import PermissionRequestPresenter

AGENT_ID = uuid4()


def _event(request: CommandExecutionApprovalRequest | FileChangeApprovalRequest | AdditionalPermissionsApprovalRequest) -> PermissionRequest:
    return PermissionRequest(request_id="approval-1", agent_id=AGENT_ID, session_name="Session 1", request=request)


def test_command_approval_shows_only_decision_essential_normalized_fields() -> None:
    prompt = PermissionRequestPresenter().present(_event(CommandExecutionApprovalRequest(
        session_identifier={}, command="pytest tests/test_ui_app.py", working_directory="C:/project", rationale="Verify the user-interface change."
    )))
    assert prompt == "\n".join(("Allow this command to run?", "Command:", "pytest tests/test_ui_app.py", "Working directory: C:/project", "Why this is needed: Verify the user-interface change."))


def test_file_change_approval_shows_paths_summary_and_rationale() -> None:
    prompt = PermissionRequestPresenter().present(_event(FileChangeApprovalRequest(
        session_identifier={}, affected_paths=("pairo/ui/presentation.py", "tests/test_permission_presentation.py"), change_summary="Add clear file-change approval details.", rationale="Make the requested approval decision informed."
    )))
    assert prompt == "\n".join(("Allow these file changes?", "Affected files:", "- pairo/ui/presentation.py", "- tests/test_permission_presentation.py", "Changes: Add clear file-change approval details.", "Why this is needed: Make the requested approval decision informed."))


def test_additional_permission_approval_shows_permission_names_not_wire_values() -> None:
    prompt = PermissionRequestPresenter().present(_event(AdditionalPermissionsApprovalRequest(
        session_identifier={}, requested_permissions={"network": {"enabled": True}}, rationale="Fetch a package."
    )))
    assert prompt == "\n".join(("Allow these additional permissions?", "Requested permissions:", "- network", "Why this is needed: Fetch a package."))


def test_file_change_approval_explains_missing_optional_details() -> None:
    assert PermissionRequestPresenter().present(_event(FileChangeApprovalRequest(session_identifier={}))) == "\n".join(("Allow these file changes?", "The agent did not provide file-change details."))
