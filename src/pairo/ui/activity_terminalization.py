"""Durable terminal states for live activity transcript cards."""

from __future__ import annotations

from enum import Enum

from .models import (
    CommandExecutionTranscriptMetadata,
    McpToolCallTranscriptMetadata,
    TranscriptEntry,
    TranscriptMetadata,
)


class ActivityTerminalOutcome(str, Enum):
    """The user-facing reason a live activity stopped."""

    INTERRUPTED = "Interrupted"
    ERROR = "Stopped after error"


class PendingActivityTerminalizer:
    """Convert pending cards to their durable activity-family representation."""

    def __init__(self, outcome: ActivityTerminalOutcome) -> None:
        self._outcome = outcome

    def terminalize(
        self, entry: TranscriptEntry[TranscriptMetadata]
    ) -> TranscriptEntry[TranscriptMetadata]:
        transcript_type = entry.metadata["type"]
        _title, separator, detail = entry.content.partition("\n\n")
        retained_detail = detail if separator else entry.content
        if transcript_type == "command_execution_progress":
            return TranscriptEntry[CommandExecutionTranscriptMetadata](
                entry.role,
                f"Command — {self._outcome.value}\n\n{retained_detail}",
                entry.detail_level,
                {"type": "command_execution"},
                entry.entry_id,
            )
        if transcript_type == "mcp_tool_call_progress":
            return TranscriptEntry[McpToolCallTranscriptMetadata](
                entry.role,
                f"MCP tool — {self._outcome.value}\n\n{retained_detail}",
                entry.detail_level,
                {"type": "mcp_tool_call"},
                entry.entry_id,
            )
        raise ValueError(f"Cannot terminalize transcript type '{transcript_type}'.")


__all__ = ["ActivityTerminalOutcome", "PendingActivityTerminalizer"]
