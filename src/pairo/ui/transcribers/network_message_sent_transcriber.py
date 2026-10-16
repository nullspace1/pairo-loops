"""Transcribe and render inter-agent network messages."""

from pairo.core.events import NetworkMessageSent
from textual.widget import Widget

from ..models import EventDetailLevel, NetworkTranscriptMetadata, TranscriptEntry
from .routed_transcript_entry import RoutedTranscriptEntry
from .text_transcript import render_text_transcript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class NetworkMessageSentTranscriber(
    Transcriber[NetworkMessageSent, NetworkTranscriptMetadata]
):
    def __init__(self) -> None:
        super().__init__(NetworkMessageSent, "network")

    def _write(
        self, event: NetworkMessageSent
    ) -> RoutedTranscriptEntry[NetworkTranscriptMetadata]:
        return RoutedTranscriptEntry(
            event.recipient_id,
            event.session_name,
            TranscriptEntry(
                "network",
                f"[{event.channel_name}] {event.sender_name}: {event.message}",
                EventDetailLevel.NORMAL,
                {"type": "network"},
            ),
        )

    def render(
        self,
        transcript: TranscriptEntry[NetworkTranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> Widget:
        return render_text_transcript(transcript, context)

