"""Transcript presentation for live command-execution progress."""

from pairo.core.events import AgentCommandExecutionProgress
from textual.widget import Widget

from ..models import CommandExecutionProgressTranscriptMetadata, EventDetailLevel, TranscriptEntry
from .activity_correlation import ActivityCorrelation
from .routed_transcript_entry import RoutedTranscriptEntry
from .text_transcript import render_text_transcript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class CommandExecutionProgressTranscriber(Transcriber[AgentCommandExecutionProgress, CommandExecutionProgressTranscriptMetadata]):
    """Show only the latest provider delta until completion supplies full output."""

    def __init__(self) -> None:
        super().__init__(AgentCommandExecutionProgress, "command_execution_progress")

    def _write(self, event: AgentCommandExecutionProgress) -> RoutedTranscriptEntry[CommandExecutionProgressTranscriptMetadata]:
        entry_id = ActivityCorrelation(event.agent_id, event.session_name, "command_execution", event.activity_id).entry_id
        snapshot = event.output_delta or "No output yet."
        return RoutedTranscriptEntry(event.agent_id, event.session_name, TranscriptEntry(
            "agent", f"Command — In progress\n\nLatest output:\n{snapshot}", EventDetailLevel.NORMAL,
            {"type": "command_execution_progress"}, entry_id,
        ), entry_id)

    def render(self, transcript: TranscriptEntry[CommandExecutionProgressTranscriptMetadata], context: TranscriptRenderContext) -> Widget:
        return render_text_transcript(transcript, context)
