"""Transcribe and render agent failures."""

from pairo.core.events import AgentError
from textual.widget import Widget

from ..models import ErrorTranscriptMetadata, EventDetailLevel, TranscriptEntry
from .routed_transcript_entry import RoutedTranscriptEntry
from .text_transcript import render_text_transcript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class AgentErrorTranscriber(Transcriber[AgentError, ErrorTranscriptMetadata]):
    def __init__(self) -> None:
        super().__init__(AgentError, "error")

    def _write(self, event: AgentError) -> RoutedTranscriptEntry[ErrorTranscriptMetadata]:
        return RoutedTranscriptEntry(
            event.agent_id,
            event.session_name,
            TranscriptEntry(
                "system",
                f"Agent error: {event.message}",
                EventDetailLevel.NORMAL,
                {"type": "error"},
            ),
        )

    def render(
        self,
        transcript: TranscriptEntry[ErrorTranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> Widget:
        return render_text_transcript(transcript, context)

