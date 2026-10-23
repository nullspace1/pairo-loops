"""Transcript presentation for completed MCP tool calls."""

import json

from pairo.core.events import AgentMcpToolCallCompleted
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from ..models import EventDetailLevel, McpToolCallTranscriptMetadata, TranscriptEntry
from .activity_correlation import ActivityCorrelation
from .routed_transcript_entry import RoutedTranscriptEntry
from .text_transcript import SourcedTranscript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class McpToolCallCompletedTranscriber(Transcriber[AgentMcpToolCallCompleted, McpToolCallTranscriptMetadata]):
    """Present MCP identity and outcome without provider wire identifiers."""

    def __init__(self) -> None:
        super().__init__(AgentMcpToolCallCompleted, "mcp_tool_call")

    def _write(self, event: AgentMcpToolCallCompleted) -> RoutedTranscriptEntry[McpToolCallTranscriptMetadata]:
        entry_id = ActivityCorrelation(event.agent_id, event.session_name, "mcp_tool_call", event.activity_id).entry_id
        details = json.dumps(
            {"Arguments": event.arguments, "Result": event.result, "Error": event.error},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        return RoutedTranscriptEntry(event.agent_id, event.session_name, TranscriptEntry(
            "agent", f"MCP {event.server} / {event.tool} — {self._outcome(event)}\n\n{details}",
            EventDetailLevel.NORMAL, {"type": "mcp_tool_call"}, entry_id,
        ), entry_id)

    def _outcome(self, event: AgentMcpToolCallCompleted) -> str:
        labels = {
            "completed": "Completed successfully",
            "failed": "Failed",
            "inProgress": "In progress",
        }
        outcome = labels[event.status]
        if event.error is None:
            return outcome
        return f"{outcome}: {event.error}"

    def render(self, transcript: TranscriptEntry[McpToolCallTranscriptMetadata], context: TranscriptRenderContext) -> Widget:
        title, _, details = transcript.content.partition("\n\n")
        return SourcedTranscript(transcript, context, Collapsible(
            Static(details or "No additional details were provided.", markup=False),
            title=title, collapsed=True, classes="completed-activity mcp-activity",
        ))
