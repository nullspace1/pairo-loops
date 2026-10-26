"""Terminal-only transcription for command execution."""

import sys
from typing import cast
from uuid import UUID, uuid4

from pairo.core.events import AgentCommandExecutionCompleted
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from ..models import CommandExecutionTranscriptMetadata, EventDetailLevel, TranscriptEntry
from .routed_transcript_entry import RoutedTranscriptEntry
from .text_transcript import SourcedTranscript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class _CommandOutputLimiter:
    """Bound a durable command transcript without hiding either end of its output."""

    _MARKER_TEMPLATE = "\n… [{} characters omitted] …\n"
    MINIMUM_LIMIT = len(_MARKER_TEMPLATE.format(sys.maxsize)) + 2

    def __init__(self, limit: int) -> None:
        if limit < self.MINIMUM_LIMIT:
            raise ValueError(
                "command_output_limit must preserve a head character, tail character, "
                "and omitted-count marker."
            )
        self._limit = limit

    def limit(self, output: str | None) -> str | None:
        if output is None or len(output) <= self._limit:
            return output
        omitted = len(output)
        while True:
            marker = self._MARKER_TEMPLATE.format(omitted)
            preserved_length = self._limit - len(marker)
            head_length = preserved_length // 2
            tail_length = preserved_length - head_length
            omitted = len(output) - head_length - tail_length
            if self._MARKER_TEMPLATE.format(omitted) == marker:
                return output[:head_length] + marker + output[-tail_length:]


class CommandExecutionTranscriber(
    Transcriber[AgentCommandExecutionCompleted, CommandExecutionTranscriptMetadata]
):
    """Persist exactly one sourced entry for each completed command."""

    DEFAULT_COMMAND_OUTPUT_LIMIT = 64 * 1024
    MINIMUM_COMMAND_OUTPUT_LIMIT = _CommandOutputLimiter.MINIMUM_LIMIT

    def __init__(
        self, command_output_limit: int = DEFAULT_COMMAND_OUTPUT_LIMIT
    ) -> None:
        super().__init__(AgentCommandExecutionCompleted, "command_execution")
        self._output_limiter = _CommandOutputLimiter(command_output_limit)

    def _write(
        self, event: AgentCommandExecutionCompleted
    ) -> RoutedTranscriptEntry[CommandExecutionTranscriptMetadata]:
        label = {
            "completed": "Completed successfully",
            "failed": "Failed",
            "declined": "Declined",
            "inProgress": "In progress",
        }[event.status]
        if event.exit_code is not None:
            label = f"{label} (exit code {event.exit_code})"
        entry = TranscriptEntry(
            "agent",
            f"Command — {label}\n\nCommand:\n{event.command}"
            f"\n\nWorking directory: {event.cwd or 'Not provided'}"
            f"\n\nOutput:\n{self._output_limiter.limit(event.output) or 'No output was provided.'}",
            EventDetailLevel.DEBUG,
            cast(CommandExecutionTranscriptMetadata, {"type": self.transcript_type}),
            uuid4(),
        )
        return RoutedTranscriptEntry(event.agent_id, event.session_name, entry)

    def render(
        self,
        transcript: TranscriptEntry[CommandExecutionTranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> Widget:
        title, _, details = transcript.content.partition("\n\n")
        return SourcedTranscript(
            transcript,
            context,
            Collapsible(
                Static(details or "No command details were provided.", markup=False),
                title=title,
                collapsed=True,
                classes="completed-activity command-activity",
            ),
        )
