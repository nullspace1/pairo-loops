"""Transcript presentation for completed commands."""

from pairo.core.events import AgentCommandExecutionCompleted
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from ..models import CommandExecutionTranscriptMetadata, EventDetailLevel, TranscriptEntry
from .activity_correlation import ActivityCorrelation
from .routed_transcript_entry import RoutedTranscriptEntry
from .text_transcript import SourcedTranscript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class CommandExecutionCompletedTranscriber(Transcriber[AgentCommandExecutionCompleted, CommandExecutionTranscriptMetadata]):
    """Keep the decision-relevant command outcome concise until expanded."""

    def __init__(self) -> None:
        super().__init__(AgentCommandExecutionCompleted, "command_execution")

    def _write(self, event: AgentCommandExecutionCompleted) -> RoutedTranscriptEntry[CommandExecutionTranscriptMetadata]:
        entry_id = ActivityCorrelation(event.agent_id, event.session_name, "command_execution", event.activity_id).entry_id
        return RoutedTranscriptEntry(event.agent_id, event.session_name, TranscriptEntry(
            "agent", f"Command — {self._outcome(event)}\n\nCommand:\n{event.command}\n\nWorking directory: {event.cwd or 'Not provided'}\n\nOutput:\n{event.output or 'No output was provided.'}", EventDetailLevel.NORMAL,
            {"type": "command_execution"}, entry_id,
        ), entry_id)

    def _outcome(self, event: AgentCommandExecutionCompleted) -> str:
        labels = {
            "completed": "Completed successfully",
            "failed": "Failed",
            "declined": "Declined",
            "inProgress": "In progress",
        }
        outcome = labels[event.status]
        if event.exit_code is None:
            return outcome
        return f"{outcome} (exit code {event.exit_code})"

    def render(self, transcript: TranscriptEntry[CommandExecutionTranscriptMetadata], context: TranscriptRenderContext) -> Widget:
        # The command is deliberately never truncated; verbose output remains opt-in.
        title, _, details = transcript.content.partition("\n\n")
        return SourcedTranscript(transcript, context, Collapsible(
            Static(details or "No command details were provided.", markup=False),
            title=title, collapsed=True, classes="completed-activity command-activity",
        ))
