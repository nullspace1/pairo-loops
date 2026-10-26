"""Terminal-only transcription for MCP tool calls."""

import json
from typing import cast
from uuid import UUID, uuid4

from pairo.core.events import AgentMcpToolCallCompleted
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from ..models import EventDetailLevel, McpToolCallTranscriptMetadata, TranscriptEntry
from .routed_transcript_entry import ActivityIdentity, RoutedTranscriptEntry
from .text_transcript import SourcedTranscript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class McpToolCallTranscriber(
    Transcriber[AgentMcpToolCallCompleted, McpToolCallTranscriptMetadata]
):
    """Persist exactly one sourced entry for each completed MCP call."""

    def __init__(self) -> None:
        super().__init__(AgentMcpToolCallCompleted, "mcp_tool_call")

    def _write(
        self, event: AgentMcpToolCallCompleted
    ) -> RoutedTranscriptEntry[McpToolCallTranscriptMetadata]:
        label = {
            "completed": "Completed successfully",
            "failed": "Failed",
            "inProgress": "In progress",
        }[event.status]
        if event.error is not None:
            label = f"{label}: {event.error}"
        details = json.dumps(
            {"Arguments": event.arguments, "Result": event.result, "Error": event.error},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        entry = TranscriptEntry(
            "agent",
            f"MCP {event.server} / {event.tool} â€” {label}\n\n{details}",
            EventDetailLevel.NORMAL,
            cast(McpToolCallTranscriptMetadata, {"type": self.transcript_type}),
            uuid4(),
        )
        identity = ActivityIdentity(
            event.agent_id, event.session_name, "mcp_tool_call", event.activity_id
        )
        return RoutedTranscriptEntry(event.agent_id, event.session_name, entry, identity)

    def render(
        self,
        transcript: TranscriptEntry[McpToolCallTranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> Widget:
        title, _, details = transcript.content.partition("\n\n")
        return SourcedTranscript(
            transcript,
            context,
            Collapsible(
                Static(details or "No additional details were provided.", markup=False),
                title=title,
                collapsed=True,
                classes="completed-activity mcp-activity",
            ),
        )
