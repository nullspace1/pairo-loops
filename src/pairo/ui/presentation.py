"""Human-readable presentation of SDK permission and activity payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Any, Literal, Protocol, TypeGuard

from pairo.core.events import PermissionRequest


_HIDDEN_TECHNICAL_FIELDS = {"id", "itemId", "threadId", "turnId", "requestId", "type"}
_FIELD_LABELS = {
    "cwd": "Working directory",
    "exitCode": "Exit code",
    "serverName": "Server",
    "aggregatedOutput": "Output",
}


class FieldNamePresenter:
    """Translate SDK field names into labels intended for people."""

    def present(self, value: str) -> str:
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


class StructuredStringDecoder:
    """Decode JSON containers embedded in otherwise scalar SDK fields."""

    def decode(self, value: Any) -> Any:
        if not isinstance(value, str) or not value.lstrip().startswith(("{", "[")):
            return value
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return value
        return decoded if isinstance(decoded, (dict, list)) else value


@dataclass(frozen=True, slots=True)
class PresentedValue:
    """Rendered value plus its structure, used by parent presenters for layout."""

    kind: Literal["scalar", "sequence", "structured"]
    lines: tuple[str, ...]


class ValuePresenter(Protocol):
    """A focused renderer for one category of SDK value."""

    def supports(self, value: object) -> bool: ...

    def present(self, value: Any, formatter: PresentationFormatter) -> PresentedValue: ...


class ScalarPresenter:
    """Present simple values without Python container syntax."""

    def supports(self, value: object) -> bool:
        return True

    def present(self, value: Any, formatter: PresentationFormatter) -> PresentedValue:
        del formatter
        if value is True:
            text = "Yes"
        elif value is False:
            text = "No"
        elif value is None:
            text = "Not specified"
        else:
            text = str(value).strip()
        return PresentedValue("scalar", (text,))


class SequencePresenter:
    """Lay out nested list-like values as readable bullet points."""

    def supports(self, value: object) -> bool:
        return _is_sequence(value)

    def present(self, value: Any, formatter: PresentationFormatter) -> PresentedValue:
        assert _is_sequence(value)
        lines: list[str] = []
        for item in value:
            presented = formatter.present_value(item)
            if presented.kind == "structured" and presented.lines:
                lines.append(f"- {presented.lines[0]}")
                lines.extend(f"  {line}" for line in presented.lines[1:])
            elif presented.kind == "sequence":
                lines.append("- Items:")
                lines.extend(f"  {line}" for line in presented.lines)
            else:
                lines.append(f"- {presented.lines[0]}")
        return PresentedValue("sequence", tuple(lines))


class StructuredPresenter:
    """Present mappings as ordered, labelled fields while hiding SDK metadata."""

    def __init__(self, field_names: FieldNamePresenter) -> None:
        self._field_names = field_names

    def supports(self, value: object) -> bool:
        return isinstance(value, Mapping)

    def present(self, value: Any, formatter: PresentationFormatter) -> PresentedValue:
        assert isinstance(value, Mapping)
        return PresentedValue("structured", self.describe(value, formatter=formatter))

    def describe(
        self,
        payload: Mapping[str, Any],
        *,
        formatter: PresentationFormatter,
        preferred_fields: Sequence[str] = (),
    ) -> tuple[str, ...]:
        ordered_keys = [key for key in preferred_fields if key in payload]
        ordered_keys.extend(key for key in payload if key not in ordered_keys)
        lines: list[str] = []
        for key in ordered_keys:
            if key in _HIDDEN_TECHNICAL_FIELDS:
                continue
            value = payload[key]
            if value in (None, "", [], {}):
                continue
            label = self._field_names.present(key)
            presented = formatter.present_value(value)
            if presented.kind == "scalar":
                lines.append(f"{label}: {presented.lines[0]}")
            else:
                lines.append(f"{label}:")
                lines.extend(f"  {line}" for line in presented.lines)
        return tuple(lines)


class PresentationFormatter:
    """Compose focused value presenters into one formatting boundary."""

    def __init__(
        self,
        *,
        field_names: FieldNamePresenter | None = None,
        decoder: StructuredStringDecoder | None = None,
        value_presenters: Sequence[ValuePresenter] | None = None,
    ) -> None:
        self.field_names = field_names or FieldNamePresenter()
        self._decoder = decoder or StructuredStringDecoder()
        self.structured = StructuredPresenter(self.field_names)
        self.sequence = SequencePresenter()
        self.scalar = ScalarPresenter()
        self._value_presenters = tuple(
            value_presenters or (self.structured, self.sequence, self.scalar)
        )

    def present_value(self, value: Any) -> PresentedValue:
        decoded = self._decoder.decode(value)
        for presenter in self._value_presenters:
            if presenter.supports(decoded):
                return presenter.present(decoded, self)
        raise ValueError(f"No presentation rule exists for {type(decoded).__name__} values.")

    def describe_fields(
        self,
        payload: Mapping[str, Any],
        *,
        preferred_fields: Sequence[str] = (),
    ) -> tuple[str, ...]:
        return self.structured.describe(
            payload,
            formatter=self,
            preferred_fields=preferred_fields,
        )


def _is_sequence(value: object) -> TypeGuard[Sequence[Any]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


_DEFAULT_FORMATTER = PresentationFormatter()


# Public compatibility wrappers for callers that use the original function API.
def humanize_field(value: str) -> str:
    """Turn camelCase and snake_case field names into readable labels."""
    return _DEFAULT_FORMATTER.field_names.present(value)


def describe_fields(
    payload: Mapping[str, Any],
    *,
    preferred_fields: Sequence[str] = (),
) -> tuple[str, ...]:
    """Flatten structured data into labelled lines without JSON syntax."""
    return _DEFAULT_FORMATTER.describe_fields(payload, preferred_fields=preferred_fields)


class PermissionRequestPresenter:
    """Explain approval requests in the terms a person needs to decide."""

    def present(self, request: PermissionRequest) -> str:
        return "\n".join(request.request.history_lines())


class PermissionHistoryPresenter:
    """Create the durable transcript entry for a pending approval."""

    def __init__(self, request_presenter: PermissionRequestPresenter | None = None) -> None:
        self._request_presenter = request_presenter or PermissionRequestPresenter()

    def present(self, request: PermissionRequest) -> str:
        return f"Approval requested\n{self._request_presenter.present(request)}"


def permission_request_history_content(request: PermissionRequest) -> str:
    """Format the durable, human-readable history for an approval request."""
    return PermissionHistoryPresenter().present(request)


