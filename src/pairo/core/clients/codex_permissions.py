"""Translate Codex approval wire messages to provider-neutral contracts."""

from __future__ import annotations

from typing import cast

from openai_codex.models import JsonObject as CodexJsonObject

from ..agent_config import JsonObject
from ..permissions import (
    ApprovalRequest,
    PermissionDecision,
    PermissionRequestType,
)


_REQUEST_TYPES = {
    "item/commandExecution/requestApproval": PermissionRequestType.COMMAND_EXECUTION,
    "item/fileChange/requestApproval": PermissionRequestType.FILE_CHANGE,
    "item/permissions/requestApproval": PermissionRequestType.ADDITIONAL_PERMISSIONS,
}


def translate_permission_request(
    method: str,
    params: CodexJsonObject | None,
    session_identifier: JsonObject,
) -> ApprovalRequest | None:
    request_type = _REQUEST_TYPES.get(method)
    if request_type is None:
        return None
    return ApprovalRequest(
        session_identifier=session_identifier,
        request_type=request_type,
        details=cast(JsonObject, params or {}),
    )


def translate_permission_decision(
    request: ApprovalRequest,
    decision: PermissionDecision,
) -> CodexJsonObject:
    if request.request_type is not PermissionRequestType.ADDITIONAL_PERMISSIONS:
        return {"decision": decision.value}
    if decision is PermissionDecision.DECLINE:
        return {"permissions": {}}

    requested = request.details.get("permissions")
    permissions = requested if isinstance(requested, dict) else {}
    response: CodexJsonObject = {"permissions": cast(CodexJsonObject, permissions)}
    if decision is PermissionDecision.ACCEPT_FOR_SESSION:
        response["scope"] = "session"
    return response


__all__ = ["translate_permission_decision", "translate_permission_request"]
