"""Terminal-only transcription for command execution."""

from typing import cast
from uuid import UUID, uuid4

from pairo.core.events import AgentCommandExecutionCompleted
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from ..models import CommandExecutionTranscriptMetadata, EventDetailLevel, TranscriptEntry
from .routed_transcript_entry import ActivityIdentity, RoutedTranscriptEntry
from .text_transcript import SourcedTranscript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class CommandExecutionTranscriber(
    Transcriber[AgentCommandExecutionCompleted, CommandExecutionTranscriptMetadata]
):
    """Persist exactly one sourced entry for each completed command."""

    def __init__(self) -> None:
        super().__init__(AgentCommandExecutionCompleted, "command_execution")

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
            f"Command â€” {label}\n\nCommand:\n{event.command}"
            f"\n\nWorking directory: {event.cwd or 'Not provided'}"
            f"\n\nOutput:\n{event.output or 'No output was provided.'}",
            EventDetailLevel.DEBUG,
            cast(CommandExecutionTranscriptMetadata, {"type": self.transcript_type}),
            uuid4(),
        )
        identity = ActivityIdentity(
            event.agent_id, event.session_name, "command_execution", event.activity_id
        )
        return RoutedTranscriptEntry(event.agent_id, event.session_name, entry, identity)

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
