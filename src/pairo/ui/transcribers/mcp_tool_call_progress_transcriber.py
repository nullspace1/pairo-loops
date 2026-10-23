"""Transcript presentation and durable ownership for live MCP calls."""
from dataclasses import replace
from typing import cast
from uuid import UUID, uuid4
from pairo.core.events import AgentMcpToolCallCompleted, AgentMcpToolCallProgress, AgentError, AgentResponseInterrupted
from textual.widget import Widget
from ..models import EventDetailLevel, McpToolCallProgressTranscriptMetadata, TranscriptEntry
from ..pairo_event_piper import on_pairo
from .routed_transcript_entry import ActivityIdentity, RoutedTranscriptEntry
from .text_transcript import render_text_transcript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext
class McpToolCallProgressTranscriber(Transcriber[AgentMcpToolCallProgress, McpToolCallProgressTranscriptMetadata]):
    def __init__(self) -> None:
        super().__init__(AgentMcpToolCallProgress,"mcp_tool_call_progress"); self._pending: dict[ActivityIdentity,TranscriptEntry[McpToolCallProgressTranscriptMetadata]]={}
    def _write(self,event:AgentMcpToolCallProgress)->RoutedTranscriptEntry[McpToolCallProgressTranscriptMetadata]:
        identity=ActivityIdentity(event.agent_id,event.session_name,"mcp_tool_call",event.activity_id); prior=self._pending.get(identity)
        return RoutedTranscriptEntry(event.agent_id,event.session_name,TranscriptEntry("agent",f"MCP tool â€” In progress\n\n{event.message or 'Workingâ€¦'}",EventDetailLevel.NORMAL,cast(McpToolCallProgressTranscriptMetadata,{"type":"mcp_tool_call_progress"}),prior.entry_id if prior else uuid4()),identity)
    async def persist(self,routed:RoutedTranscriptEntry[McpToolCallProgressTranscriptMetadata])->None:
        assert routed.activity is not None and self._mutations is not None; prior=self._pending.get(routed.activity); self._pending[routed.activity]=routed.entry
        if prior is None: await self._mutations.add(routed)
        else: await self._mutations.update(routed)
    @on_pairo(AgentMcpToolCallCompleted)
    async def completed(self,event:AgentMcpToolCallCompleted)->None:
        identity=ActivityIdentity(event.agent_id,event.session_name,"mcp_tool_call",event.activity_id); prior=self._pending.pop(identity,None)
        if self._mutations is None:return
        routed=McpToolCallCompletedTranscriber().write(event)
        if prior is not None: await self._mutations.update(replace(routed,entry=replace(routed.entry,entry_id=prior.entry_id)))
        else: await self._mutations.add(routed)
    @on_pairo(AgentResponseInterrupted)
    async def interrupted(self,event:AgentResponseInterrupted)->None: await self._terminalize(event.agent_id,event.session_name,"Interrupted")
    @on_pairo(AgentError)
    async def errored(self,event:AgentError)->None: await self._terminalize(event.agent_id,event.session_name,"Stopped after error")
    async def _terminalize(self,agent_id:UUID,session_name:str,label:str)->None:
        if self._mutations is None:return
        matching=[(identity,entry) for identity,entry in self._pending.items() if identity.agent_id==agent_id and identity.session_name==session_name]
        for identity,entry in matching:
            self._pending.pop(identity,None); final=replace(entry,content=entry.content.replace("In progress",label),metadata=cast(McpToolCallProgressTranscriptMetadata,{"type":"mcp_tool_call"})); await self._mutations.update(RoutedTranscriptEntry(agent_id,session_name,final,identity))
    def render(self,transcript:TranscriptEntry[McpToolCallProgressTranscriptMetadata],context:TranscriptRenderContext)->Widget:return render_text_transcript(transcript,context)
from .mcp_tool_call_completed_transcriber import McpToolCallCompletedTranscriber
