"""Human-readable presentation of SDK permission and activity payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Any

from pairo.core.events import PermissionRequest


_HIDDEN_TECHNICAL_FIELDS = {
    "id",
    "itemId",
    "threadId",
    "turnId",
    "requestId",
    "type",
}

_FIELD_LABELS = {
    "cwd": "Working directory",
    "exitCode": "Exit code",
    "serverName": "Server",
    "aggregatedOutput": "Output",
}


def humanize_field(value: str) -> str:
    """Turn camelCase and snake_case field names into readable labels."""
    if value in _FIELD_LABELS:
        return _FIELD_LABELS[value]
    words: list[str] = []
    current = ""
    for character in value.replace("_", " "):
        if character == " ":
            if current:
                words.append(current)
                current = ""
        elif character.isupper() and current:
            words.append(current)
            current = character.lower()
        else:
            current += character
    if current:
        words.append(current)
    return " ".join(words).capitalize()


def _scalar_text(value: Any) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if value is None:
        return "Not specified"
    return str(value).strip()


def describe_fields(
    payload: Mapping[str, Any],
    *,
    preferred_fields: Sequence[str] = (),
) -> tuple[str, ...]:
    """Flatten structured data into labeled lines without JSON syntax."""
    ordered_keys = [key for key in preferred_fields if key in payload]
    ordered_keys.extend(key for key in payload if key not in ordered_keys)
    lines: list[str] = []
    for key in ordered_keys:
        if key in _HIDDEN_TECHNICAL_FIELDS:
            continue
        value = payload[key]
        if value in (None, "", [], {}):
            continue
        label = humanize_field(key)
        decoded = _structured_string(value)
        display_value = decoded if decoded is not None else value
        if isinstance(display_value, Mapping):
            nested = describe_fields(display_value)
            if nested:
                lines.append(f"{label}:")
                lines.extend(f"  {line}" for line in nested)
        elif _is_sequence(display_value):
            lines.append(f"{label}:")
            lines.extend(_describe_sequence(display_value, indent="  "))
        else:
            lines.append(f"{label}: {_scalar_text(display_value)}")
    return tuple(lines)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def _describe_sequence(values: Sequence[Any], *, indent: str) -> tuple[str, ...]:
    """Render arbitrarily nested sequences without container repr syntax."""
    lines: list[str] = []
    for item in values:
        decoded = _structured_string(item)
        display_item = decoded if decoded is not None else item
        if isinstance(display_item, Mapping):
            nested = describe_fields(display_item)
            if nested:
                lines.append(f"{indent}- {nested[0]}")
                lines.extend(f"{indent}  {line}" for line in nested[1:])
        elif _is_sequence(display_item):
            lines.append(f"{indent}- Items:")
            lines.extend(_describe_sequence(display_item, indent=f"{indent}  "))
        else:
            lines.append(f"{indent}- {_scalar_text(display_item)}")
    return tuple(lines)


def _structured_string(value: Any) -> Any:
    if not isinstance(value, str) or not value.lstrip().startswith(("{", "[")):
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, (dict, list)) else None


@dataclass(frozen=True, slots=True)
class ActivityPresentation:
    """Readable title and details for one completed SDK item."""

    title: str
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        # Keep the activity heading visually distinct from its structured body.
        return "\n\n".join((self.title, "\n".join(self.lines)))


class CompletedActivityPresenter:
    """Present completed technical activity without leaking wire-format JSON."""

    _TITLES = {
        "commandExecution": "Command completed",
        "fileChange": "File change completed",
        "mcpToolCall": "MCP tool call completed",
        "webSearch": "Web search completed",
    }
    _PREFERRED_FIELDS = {
        "commandExecution": ("command", "cwd", "status", "exitCode", "aggregatedOutput", "output"),
        "fileChange": ("path", "changes", "status", "result"),
        "mcpToolCall": ("server", "serverName", "tool", "name", "arguments", "status", "result"),
        "webSearch": ("query", "queries", "status", "result"),
    }

    def present(self, item: Mapping[str, Any]) -> ActivityPresentation:
        item_type = str(item.get("type", "activity"))
        title = self._TITLES.get(item_type, f"{humanize_field(item_type)} completed")
        lines = describe_fields(
            item,
            preferred_fields=self._PREFERRED_FIELDS.get(item_type, ()),
        )
        if not lines:
            # Provider identifiers cannot help a person understand the activity
            # and should never be exposed as a substitute for useful detail.
            lines = ("No additional details were provided.",)
        return ActivityPresentation(title, lines)


class PermissionRequestPresenter:
    """Explain approval requests in the terms a person needs to decide."""

    _TITLES = {
        "command execution": "Command execution approval",
        "file change": "File change approval",
        "additional permissions": "Additional permissions approval",
    }
    _PREFERRED_FIELDS = {
        "command execution": ("command", "cwd", "reason"),
        "file change": ("path", "paths", "changes", "reason"),
        "additional permissions": ("permissions", "reason"),
    }

    def present(self, request: PermissionRequest) -> str:
        if request.request_type == "command execution":
            return self._present_command(request)
        if request.request_type == "file change":
            return self._present_file_change(request)
        title = self._TITLES.get(
            request.request_type,
            f"{humanize_field(request.request_type)} approval",
        )
        lines = describe_fields(
            request.request,
            preferred_fields=self._PREFERRED_FIELDS.get(request.request_type, ()),
        )
        if not lines:
            lines = ("The agent did not provide additional details.",)
        return "\n".join((title, *lines))

    def _present_command(self, request: PermissionRequest) -> str:
        """Put the command, where it runs, and why first in approval prompts."""
        details = request.request
        lines = ["Allow this command to run?"]
        command = details.get("command")
        if command not in (None, ""):
            lines.extend(("Command:", str(command)))
        else:
            lines.append("The agent did not provide a command.")

        for key, label in (
            ("cwd", "Working directory"),
            ("reason", "Why this is needed"),
            ("justification", "Why this is needed"),
            ("sandboxPermissions", "Sandbox permissions"),
        ):
            value = details.get(key)
            if value not in (None, "", [], {}):
                lines.append(f"{label}: {_scalar_text(value)}")

        displayed = {"command", "cwd", "reason", "justification", "sandboxPermissions"}
        remaining = {key: value for key, value in details.items() if key not in displayed}
        lines.extend(describe_fields(remaining))
        return "\n".join(lines)
    def _present_file_change(self, request: PermissionRequest) -> str:
        """Put the affected files and proposed edits before transport details."""
        details = request.request
        lines = ["Allow these file changes?"]
        path = details.get("path")
        paths = details.get("paths")
        if path not in (None, ""):
            lines.append(f"Affected file: {_scalar_text(path)}")
        if paths not in (None, "", [], {}):
            lines.append("Affected files:")
            lines.extend(_describe_sequence(paths, indent="") if _is_sequence(paths) else (f"- {_scalar_text(paths)}",))
        changes = details.get("changes")
        if changes not in (None, "", [], {}):
            lines.extend(describe_fields({"changes": changes}))

        for key in ("reason", "justification", "rationale"):
            value = details.get(key)
            if value not in (None, "", [], {}):
                lines.append(f"Why this is needed: {_scalar_text(value)}")

        displayed = {"path", "paths", "changes", "reason", "justification", "rationale"}
        remaining = {key: value for key, value in details.items() if key not in displayed}
        lines.extend(describe_fields(remaining))
        if len(lines) == 1:
            lines.append("The agent did not provide file-change details.")
        return "\n".join(lines)


def permission_request_history_content(request: PermissionRequest) -> str:
    """Format the durable, human-readable history for an approval request."""
    return f"Approval requested\n{PermissionRequestPresenter().present(request)}"
