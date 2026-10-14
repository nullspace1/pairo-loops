"""Transcribe and render user and agent messages."""

from pairo.core.events import MessageSent
from textual.widget import Widget

from ..models import EventDetailLevel, MessageTranscriptMetadata, TranscriptEntry
from .routed_transcript_entry import RoutedTranscriptEntry
from .text_transcript import render_text_transcript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class MessageSentTranscriber(Transcriber[MessageSent, MessageTranscriptMetadata]):
    def __init__(self) -> None:
        super().__init__(MessageSent, "message")

    def _write(self, event: MessageSent) -> RoutedTranscriptEntry[MessageTranscriptMetadata]:
        return RoutedTranscriptEntry(
            event.agent_id,
            event.session_name,
            TranscriptEntry(
                event.source, event.message, EventDetailLevel.NORMAL, {"type": "message"}
            ),
        )

    def render(
        self,
        transcript: TranscriptEntry[MessageTranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> Widget:
        return render_text_transcript(transcript, context)
