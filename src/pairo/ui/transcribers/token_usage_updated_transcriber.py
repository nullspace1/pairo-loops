"""Transcribe and render provider token accounting snapshots."""

from pairo.core.events import AgentTokenUsageUpdated
from textual.widget import Widget

from ..models import EventDetailLevel, TokenUsageTranscriptMetadata, TranscriptEntry
from .routed_transcript_entry import RoutedTranscriptEntry
from .text_transcript import render_text_transcript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class TokenUsageUpdatedTranscriber(
    Transcriber[AgentTokenUsageUpdated, TokenUsageTranscriptMetadata]
):
    def __init__(self) -> None:
        super().__init__(AgentTokenUsageUpdated, "token_usage")

    def _write(
        self, event: AgentTokenUsageUpdated
    ) -> RoutedTranscriptEntry[TokenUsageTranscriptMetadata]:
        context_window = (
            f"; context window: {event.model_context_window:,}"
            if event.model_context_window is not None
            else ""
        )
        return RoutedTranscriptEntry(
            event.agent_id,
            event.session_name,
            TranscriptEntry(
                "usage",
                (
                    "Token usage — "
                    f"turn: {event.last.input_tokens:,} input "
                    f"({event.last.cached_input_tokens:,} cached), "
                    f"{event.last.output_tokens:,} output "
                    f"({event.last.reasoning_output_tokens:,} reasoning), "
                    f"{event.last.total_tokens:,} total; "
                    f"session: {event.total.total_tokens:,} total{context_window}."
                ),
                EventDetailLevel.DEBUG,
                {"type": "token_usage"},
            ),
        )

    def render(
        self,
        transcript: TranscriptEntry[TokenUsageTranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> Widget:
        return render_text_transcript(transcript, context)
