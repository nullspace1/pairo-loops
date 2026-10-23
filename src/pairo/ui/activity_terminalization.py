"""Durable terminal states for live activity transcript cards."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from .models import CommandExecutionTranscriptMetadata, McpToolCallTranscriptMetadata, TranscriptEntry, TranscriptMetadata


class ActivityTerminalOutcome(str, Enum):
    """The user-facing reason a live activity stopped."""

    INTERRUPTED = "Interrupted"
    ERROR = "Stopped after error"


class PendingActivityFamilyTerminalizer(ABC):
    """Terminalize one persisted progress family without UI type branching."""

    @property
    @abstractmethod
    def progress_transcript_type(self) -> str:
        """The durable metadata key handled by this family."""

    @abstractmethod
    def terminalize(self, entry: TranscriptEntry[TranscriptMetadata], outcome: ActivityTerminalOutcome) -> TranscriptEntry[TranscriptMetadata]:
        """Replace this family's progress representation with its terminal card."""

    def retained_detail(self, entry: TranscriptEntry[TranscriptMetadata]) -> str:
        _title, separator, detail = entry.content.partition("\n\n")
        return detail if separator else entry.content


class CommandExecutionPendingTerminalizer(PendingActivityFamilyTerminalizer):
    @property
    def progress_transcript_type(self) -> str:
        return "command_execution_progress"

    def terminalize(self, entry: TranscriptEntry[TranscriptMetadata], outcome: ActivityTerminalOutcome) -> TranscriptEntry[TranscriptMetadata]:
        metadata: CommandExecutionTranscriptMetadata = {"type": "command_execution"}
        return TranscriptEntry(entry.role, f"Command — {outcome.value}\n\n{self.retained_detail(entry)}", entry.detail_level, metadata, entry.entry_id)


class McpToolCallPendingTerminalizer(PendingActivityFamilyTerminalizer):
    @property
    def progress_transcript_type(self) -> str:
        return "mcp_tool_call_progress"

    def terminalize(self, entry: TranscriptEntry[TranscriptMetadata], outcome: ActivityTerminalOutcome) -> TranscriptEntry[TranscriptMetadata]:
        metadata: McpToolCallTranscriptMetadata = {"type": "mcp_tool_call"}
        return TranscriptEntry(entry.role, f"MCP tool — {outcome.value}\n\n{self.retained_detail(entry)}", entry.detail_level, metadata, entry.entry_id)


class PendingActivityTerminalizer:
    """Route durable progress entries to registered family terminalizers."""

    def __init__(self, outcome: ActivityTerminalOutcome, handlers: tuple[PendingActivityFamilyTerminalizer, ...] | None = None) -> None:
        self._outcome = outcome
        registered = handlers or (CommandExecutionPendingTerminalizer(), McpToolCallPendingTerminalizer())
        self._handlers = {handler.progress_transcript_type: handler for handler in registered}
        if len(self._handlers) != len(registered):
            raise ValueError("Each pending activity family must have one terminalizer.")

    def terminalize(self, entry: TranscriptEntry[TranscriptMetadata]) -> TranscriptEntry[TranscriptMetadata]:
        handler = self._handlers.get(entry.metadata["type"])
        if handler is None:
            raise ValueError(f"Cannot terminalize transcript type '{entry.metadata['type']}'.")
        return handler.terminalize(entry, self._outcome)


__all__ = ["ActivityTerminalOutcome", "CommandExecutionPendingTerminalizer", "McpToolCallPendingTerminalizer", "PendingActivityFamilyTerminalizer", "PendingActivityTerminalizer"]
