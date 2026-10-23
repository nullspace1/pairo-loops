"""Transcript presentation for completed MCP calls."""

import json

from uuid import uuid4

from pairo.core.events import AgentMcpToolCallCompleted

from textual.widget import Widget

from textual.widgets import Collapsible, Static

from ..models import EventDetailLevel,McpToolCallTranscriptMetadata,TranscriptEntry

from .routed_transcript_entry import ActivityIdentity,RoutedTranscriptEntry

from .text_transcript import SourcedTranscript

from .transcriber import Transcriber

from .transcript_render_context import TranscriptRenderContext

class McpToolCallCompletedTranscriber(Transcriber[AgentMcpToolCallCompleted,McpToolCallTranscriptMetadata]):

    def __init__(self)->None:super().__init__(AgentMcpToolCallCompleted,"mcp_tool_call")

    def request(self, event: object) -> bool:

        """The paired live owner consumes completion from the broadcast."""

        return False



    def _write(self,event:AgentMcpToolCallCompleted)->RoutedTranscriptEntry[McpToolCallTranscriptMetadata]:

        label={"completed":"Completed successfully","failed":"Failed","inProgress":"In progress"}[event.status]; label=label if event.error is None else f"{label}: {event.error}"

        details=json.dumps({"Arguments":event.arguments,"Result":event.result,"Error":event.error},ensure_ascii=False,indent=2,default=str)

        return RoutedTranscriptEntry(event.agent_id,event.session_name,TranscriptEntry("agent",f"MCP {event.server} / {event.tool} â€” {label}\n\n{details}",EventDetailLevel.NORMAL,{"type":"mcp_tool_call"},uuid4()),ActivityIdentity(event.agent_id,event.session_name,"mcp_tool_call",event.activity_id))

    def render(self,transcript:TranscriptEntry[McpToolCallTranscriptMetadata],context:TranscriptRenderContext)->Widget:

        title,_,details=transcript.content.partition("\n\n");return SourcedTranscript(transcript,context,Collapsible(Static(details or "No additional details were provided.",markup=False),title=title,collapsed=True,classes="completed-activity mcp-activity"))
