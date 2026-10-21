"""Translate Codex approval wire messages to provider-neutral contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from openai_codex.models import JsonObject as CodexJsonObject
from pydantic import JsonValue, TypeAdapter, ValidationError

from ..agent.json_object import JsonObject
from ..permissions import (
    AdditionalPermissionsApprovalRequest,
    ApprovalRequest,
    CommandExecutionApprovalRequest,
    FileChangeApprovalRequest,
    PermissionDecision,
)


_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class _OptionalFields:
    """Safely read optional, provider-owned fields without leaking wire data."""

    def __init__(self, values: Mapping[str, JsonValue]) -> None:
        self._values = values

    def text(self, *names: str) -> str | None:
        for name in names:
            value = self._values.get(name)
            if isinstance(value, str) and value:
                return value
        return None

    def paths(self) -> tuple[str, ...]:
        paths: list[str] = []
        single_path = self.text("path")
        if single_path is not None:
            paths.append(single_path)
        multiple_paths = self._values.get("paths")
        if isinstance(multiple_paths, list):
            paths.extend(path for path in multiple_paths if isinstance(path, str) and path)
        return tuple(dict.fromkeys(paths))

    def file_change_details(self) -> tuple[tuple[str, ...], str]:
        """Extract safe decision details from scalar or structured change data."""
        paths = list(self.paths())
        summary = self.text("summary") or ""
        changes = self._values.get("changes")
        if isinstance(changes, str):
            summary = changes
        elif isinstance(changes, Mapping):
            structured_changes = _StructuredFileChanges(changes)
            paths.extend(structured_changes.paths())
            summary = summary or structured_changes.summary()
        return tuple(dict.fromkeys(paths)), summary

    def permissions(self) -> JsonObject:
        value = self._values.get("permissions")
        try:
            normalized = _JSON_VALUE_ADAPTER.validate_python(value)
        except ValidationError:
            return {}
        if isinstance(normalized, dict) and all(
            isinstance(key, str) for key in normalized
        ):
            return normalized
        return {}


class _StructuredFileChanges:
    """Read the useful, typed subset of a structured file-change payload."""

    def __init__(self, values: Mapping[str, JsonValue]) -> None:
        self._values = values

    def paths(self) -> tuple[str, ...]:
        paths: list[str] = []
        for name in ("paths", "files", "affectedPaths"):
            paths.extend(self._paths_in(self._values.get(name)))
        return tuple(dict.fromkeys(paths))

    def summary(self) -> str:
        for name in ("summary", "description", "message"):
            value = self._values.get(name)
            if isinstance(value, str) and value:
                return value
        return ""

    def _paths_in(self, value: JsonValue | None) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        paths: list[str] = []
        for entry in value:
            if isinstance(entry, str) and entry:
                paths.append(entry)
            elif isinstance(entry, Mapping):
                path = self._path_in(entry)
                if path is not None:
                    paths.append(path)
        return tuple(paths)

    def _path_in(self, value: Mapping[str, JsonValue]) -> str | None:
        for name in ("path", "file", "filePath"):
            candidate = value.get(name)
            if isinstance(candidate, str) and candidate:
                return candidate
        return None


class _CodexRequestTranslator(ABC):
    @abstractmethod
    def translate(
        self, fields: _OptionalFields, session_identifier: JsonObject
    ) -> ApprovalRequest:
        """Create the typed request for one supported Codex wire method."""


class _CommandExecutionTranslator(_CodexRequestTranslator):
    def translate(self, fields: _OptionalFields, session_identifier: JsonObject) -> ApprovalRequest:
        return CommandExecutionApprovalRequest(
            session_identifier=session_identifier,
            command=fields.text("command") or "",
            working_directory=fields.text("cwd", "workingDirectory"),
            rationale=fields.text("reason", "justification", "rationale"),
        )


class _FileChangeTranslator(_CodexRequestTranslator):
    def translate(self, fields: _OptionalFields, session_identifier: JsonObject) -> ApprovalRequest:
        affected_paths, change_summary = fields.file_change_details()
        return FileChangeApprovalRequest(
            session_identifier=session_identifier,
            affected_paths=affected_paths,
            change_summary=change_summary,
            rationale=fields.text("reason", "justification", "rationale"),
        )


class _AdditionalPermissionsTranslator(_CodexRequestTranslator):
    def translate(self, fields: _OptionalFields, session_identifier: JsonObject) -> ApprovalRequest:
        return AdditionalPermissionsApprovalRequest(
            session_identifier=session_identifier,
            requested_permissions=fields.permissions(),
            rationale=fields.text("reason", "justification", "rationale"),
        )


_TRANSLATORS: dict[str, _CodexRequestTranslator] = {
    "item/commandExecution/requestApproval": _CommandExecutionTranslator(),
    "item/fileChange/requestApproval": _FileChangeTranslator(),
    "item/permissions/requestApproval": _AdditionalPermissionsTranslator(),
}


def translate_permission_request(
    method: str, params: CodexJsonObject | None, session_identifier: JsonObject
) -> ApprovalRequest | None:
    translator = _TRANSLATORS.get(method)
    if translator is None:
        return None
    values: Mapping[str, JsonValue] = params if isinstance(params, Mapping) else {}
    return translator.translate(_OptionalFields(values), session_identifier)


def translate_permission_decision(
    request: ApprovalRequest, decision: PermissionDecision
) -> CodexJsonObject:
    return request.decision_response(decision)


__all__ = ["translate_permission_decision", "translate_permission_request"]
