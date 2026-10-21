"""Approval contract normalization and structured permission-card coverage."""

from typing import TypeAlias
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from textual.containers import Vertical
from textual.widgets import Static

from pairo.core.clients.codex_permissions import translate_permission_decision, translate_permission_request
from pairo.core.events import PermissionRequest
from pairo.core.permissions import AdditionalPermissionsApprovalRequest, ApprovalRequest, CommandExecutionApprovalRequest, FileChangeApprovalRequest, PermissionDecision
from pairo.ui.models import PermissionRequestTranscriptMetadata, TranscriptEntry
from pairo.ui.transcribers import AdditionalPermissionsPermissionDetails, CommandExecutionPermissionDetails, FileChangePermissionDetails, PermissionDetailWidget, PermissionRequestCard

ApprovalRequestClass: TypeAlias = type[CommandExecutionApprovalRequest] | type[FileChangeApprovalRequest] | type[AdditionalPermissionsApprovalRequest]

@pytest.mark.parametrize(("method", "expected_type"), [("item/commandExecution/requestApproval", CommandExecutionApprovalRequest), ("item/fileChange/requestApproval", FileChangeApprovalRequest), ("item/permissions/requestApproval", AdditionalPermissionsApprovalRequest)])
def test_translator_normalizes_each_supported_request_variant(method: str, expected_type: ApprovalRequestClass) -> None:
    request = translate_permission_request(method, None, {"provider": "codex"})
    assert request is not None
    assert isinstance(request, expected_type)

def test_noisy_command_payload_retains_only_essential_widget_fields() -> None:
    request = translate_permission_request("item/commandExecution/requestApproval", {"command": "git status", "cwd": "C:/workspace", "justification": "Inspect changes.", "threadId": "hidden-thread", "turnId": "hidden-turn", "itemId": "hidden-item", "environmentId": "hidden-environment", "createdAt": "hidden-time", "availableDecisions": ["accept"], "commandActions": [{"type": "shell"}], "proposedExecPolicyAmendment": {"network": True}, "unknown": "hidden"}, {"provider": "codex"})
    assert request == CommandExecutionApprovalRequest(session_identifier={"provider": "codex"}, command="git status", working_directory="C:/workspace", rationale="Inspect changes.")
    visible = _widget_text(CommandExecutionPermissionDetails(request))
    assert "git status" in visible
    assert "C:/workspace" in visible
    assert "Inspect changes." in visible
    for hidden in ("hidden-thread", "hidden-turn", "hidden-item", "hidden-environment", "hidden-time", "shell", "network", "unknown", "availableDecisions"):
        assert hidden not in visible

def test_minimal_and_malformed_optional_fields_are_safe() -> None:
    request = translate_permission_request("item/fileChange/requestApproval", {"path": 4, "paths": ["a.py", 5], "changes": {}, "reason": []}, {})
    assert request == FileChangeApprovalRequest(session_identifier={}, affected_paths=("a.py",))
    assert translate_permission_request("unknown/method", {}, {}) is None

def test_structured_file_changes_keep_safe_paths_and_summary() -> None:
    request = translate_permission_request("item/fileChange/requestApproval", {"changes": {"files": [{"path": "src/pairo/ui/app.py"}, {"filePath": "tests/test_ui.py"}], "summary": "Render fields separately.", "rawPatch": "must not be shown"}}, {})
    assert request == FileChangeApprovalRequest(session_identifier={}, affected_paths=("src/pairo/ui/app.py", "tests/test_ui.py"), change_summary="Render fields separately.")
    visible = _widget_text(FileChangePermissionDetails(request))
    assert "src/pairo/ui/app.py" in visible
    assert "Render fields separately." in visible
    assert "rawPatch" not in visible

def test_malformed_nested_additional_permissions_are_normalized_safely() -> None:
    request = translate_permission_request("item/permissions/requestApproval", {"permissions": {"network": object()}}, {})  # type: ignore[dict-item]
    assert request == AdditionalPermissionsApprovalRequest(session_identifier={})

def test_additional_permission_response_preserves_wire_semantics() -> None:
    request = AdditionalPermissionsApprovalRequest(session_identifier={}, requested_permissions={"network": {"enabled": True}})
    assert translate_permission_decision(request, PermissionDecision.ACCEPT_FOR_SESSION) == {"permissions": {"network": {"enabled": True}}, "scope": "session"}
    assert translate_permission_decision(request, PermissionDecision.DECLINE) == {"permissions": {}}

@pytest.mark.parametrize(("approval_request", "detail_type"), [(CommandExecutionApprovalRequest(session_identifier={}, command="echo ok"), CommandExecutionPermissionDetails), (FileChangeApprovalRequest(session_identifier={}, affected_paths=("a.py",)), FileChangePermissionDetails), (AdditionalPermissionsApprovalRequest(session_identifier={}, requested_permissions={"network": {}}), AdditionalPermissionsPermissionDetails)])
def test_card_selects_a_specialized_detail_widget(approval_request: ApprovalRequest, detail_type: type[PermissionDetailWidget]) -> None:
    event = PermissionRequest(request_id="r", agent_id=uuid4(), session_name="s", request=approval_request)
    card = PermissionRequestCard(event_to_entry(event), event, _ignore_answer, lambda decision: decision)
    assert isinstance(next(iter(card.compose())), detail_type)

async def _ignore_answer(decision: str, card: PermissionRequestCard) -> None:
    return None

def event_to_entry(event: PermissionRequest) -> TranscriptEntry[PermissionRequestTranscriptMetadata]:
    from pairo.ui.transcribers import PermissionRequestTranscriber
    routed = PermissionRequestTranscriber().write(event)
    assert routed is not None
    return routed.entry

def test_command_detail_composes_labeled_wrapping_fields() -> None:
    long_command = "echo " + "long-command " * 50
    detail = CommandExecutionPermissionDetails(CommandExecutionApprovalRequest(session_identifier={}, command=long_command, working_directory="C:/workspace", rationale="Inspect the workspace."))
    fields = [child for child in detail.compose() if isinstance(child, Vertical)]
    assert len(fields) == 3
    assert _widget_text(detail) == "\n".join(("Command execution approval", "Command", long_command, "Working directory", "C:/workspace", "Rationale", "Inspect the workspace."))
    assert any(child.has_class("permission-field-value") for child in fields[0].compose())

def test_file_change_detail_composes_labeled_fields_and_rows() -> None:
    detail = FileChangePermissionDetails(FileChangeApprovalRequest(session_identifier={}, affected_paths=("src/a.py", "tests/test_a.py"), change_summary="Add approval details.", rationale="Make the decision informed."))
    assert _widget_text(detail) == "\n".join(("File change approval", "Affected files", "src/a.py", "tests/test_a.py", "Change summary", "Add approval details.", "Rationale", "Make the decision informed."))

def test_additional_permissions_compose_names_without_mapping_dump() -> None:
    detail = AdditionalPermissionsPermissionDetails(AdditionalPermissionsApprovalRequest(session_identifier={}, requested_permissions={"network": {"hosts": ["internal.example"]}, "filesystem": {}}, rationale="Reach the internal service."))
    visible = _widget_text(detail)
    assert "Requested permissions\nnetwork\nfilesystem" in visible
    assert "Reach the internal service." in visible
    assert "hosts" not in visible
    assert "internal.example" not in visible
    assert "{" not in visible

def _widget_text(widget: PermissionDetailWidget) -> str:
    rendered: list[str] = []
    for child in widget.compose():
        if isinstance(child, Static):
            rendered.append(str(child.content))
        elif isinstance(child, Vertical):
            rendered.extend(str(field.content) for field in child.compose() if isinstance(field, Static))
    return "\n".join(rendered)

def test_serialized_approval_reconstructs_as_its_discriminated_variant() -> None:
    request = FileChangeApprovalRequest(session_identifier={}, affected_paths=("a.py",), change_summary="Edit")
    restored = TypeAdapter(ApprovalRequest).validate_python(request.model_dump(mode="json"))
    assert restored == request
