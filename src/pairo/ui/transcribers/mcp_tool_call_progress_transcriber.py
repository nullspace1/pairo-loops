"""Transcript presentation for live MCP tool-call progress."""

from pairo.core.events import AgentMcpToolCallProgress
from textual.widget import Widget

from ..models import EventDetailLevel, McpToolCallProgressTranscriptMetadata, TranscriptEntry
from .activity_correlation import ActivityCorrelation
from .routed_transcript_entry import RoutedTranscriptEntry
from .text_transcript import render_text_transcript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class McpToolCallProgressTranscriber(Transcriber[AgentMcpToolCallProgress, McpToolCallProgressTranscriptMetadata]):
    """Render a concise, sourced status snapshot without wire identifiers."""

    def __init__(self) -> None:
        super().__init__(AgentMcpToolCallProgress, "mcp_tool_call_progress")

    def _write(self, event: AgentMcpToolCallProgress) -> RoutedTranscriptEntry[McpToolCallProgressTranscriptMetadata]:
        entry_id = ActivityCorrelation(event.agent_id, event.session_name, "mcp_tool_call", event.activity_id).entry_id
        message = event.message or "Working…"
        return RoutedTranscriptEntry(event.agent_id, event.session_name, TranscriptEntry(
            "agent", f"MCP tool — In progress\n\n{message}", EventDetailLevel.NORMAL,
            {"type": "mcp_tool_call_progress"}, entry_id,
        ), entry_id)

    def render(self, transcript: TranscriptEntry[McpToolCallProgressTranscriptMetadata], context: TranscriptRenderContext) -> Widget:
        return render_text_transcript(transcript, context)
