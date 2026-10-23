"""Stateful transcription for every MCP tool-call event."""

import json
from dataclasses import replace
from typing import cast
from uuid import UUID, uuid4

from pairo.core.events import AgentError, AgentMcpToolCallCompleted, AgentMcpToolCallProgress, AgentResponseInterrupted
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from ..models import EventDetailLevel, McpToolCallProgressTranscriptMetadata, McpToolCallTranscriptMetadata, TranscriptEntry, TranscriptMetadata
from ..pairo_event_piper import on_pairo
from .routed_transcript_entry import ActivityIdentity, RoutedTranscriptEntry
from .text_transcript import SourcedTranscript, render_text_transcript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class McpToolCallTranscriber(Transcriber[AgentMcpToolCallProgress, McpToolCallProgressTranscriptMetadata]):
    """Own MCP transcript state from the first update through terminal state."""

    completed_transcript_type = "mcp_tool_call"

    def __init__(self) -> None:
        super().__init__(AgentMcpToolCallProgress, "mcp_tool_call_progress")
        self._pending: dict[ActivityIdentity, TranscriptEntry[McpToolCallProgressTranscriptMetadata]] = {}

    def _write(self, event: AgentMcpToolCallProgress) -> RoutedTranscriptEntry[McpToolCallProgressTranscriptMetadata]:
        identity = self._identity(event.agent_id, event.session_name, event.activity_id)
        prior = self._pending.get(identity)
        entry = TranscriptEntry("agent", f"MCP tool — In progress\n\n{event.message or 'Working…'}", EventDetailLevel.NORMAL, cast(McpToolCallProgressTranscriptMetadata, {"type": self.transcript_type}), prior.entry_id if prior else uuid4())
        return RoutedTranscriptEntry(event.agent_id, event.session_name, entry, identity)

    async def persist(self, routed: RoutedTranscriptEntry[McpToolCallProgressTranscriptMetadata]) -> None:
        assert routed.activity is not None and self._mutations is not None
        previous = self._pending.get(routed.activity)
        if previous is None:
            if await self._mutations.add(routed):
                self._pending[routed.activity] = routed.entry
        else:
            await self._mutations.update(routed)
            self._pending[routed.activity] = routed.entry

    @on_pairo(AgentMcpToolCallCompleted)
    async def completed(self, event: AgentMcpToolCallCompleted) -> None:
        identity = self._identity(event.agent_id, event.session_name, event.activity_id)
        previous = self._pending.pop(identity, None)
        if self._mutations is None:
            return
        routed = self.write_completion(event)
        if previous is None:
            await self._mutations.add(routed)
            return
        await self._mutations.update(replace(routed, entry=replace(routed.entry, entry_id=previous.entry_id)))

    @on_pairo(AgentResponseInterrupted)
    async def interrupted(self, event: AgentResponseInterrupted) -> None:
        await self._terminalize(event.agent_id, event.session_name, "Interrupted")

    @on_pairo(AgentError)
    async def errored(self, event: AgentError) -> None:
        await self._terminalize(event.agent_id, event.session_name, "Stopped after error")

    async def _terminalize(self, agent_id: UUID, session_name: str, label: str) -> None:
        if self._mutations is None:
            return
        matching = [(identity, entry) for identity, entry in self._pending.items() if identity.agent_id == agent_id and identity.session_name == session_name]
        for identity, entry in matching:
            self._pending.pop(identity, None)
            final = replace(entry, content=entry.content.replace("In progress", label), metadata=cast(McpToolCallProgressTranscriptMetadata, {"type": self.completed_transcript_type}))
            await self._mutations.update(RoutedTranscriptEntry(agent_id, session_name, final, identity))

    def write_completion(self, event: AgentMcpToolCallCompleted) -> RoutedTranscriptEntry[McpToolCallTranscriptMetadata]:
        """Build the terminal snapshot owned by this state machine."""
        identity = self._identity(event.agent_id, event.session_name, event.activity_id)
        label = {"completed": "Completed successfully", "failed": "Failed", "inProgress": "In progress"}[event.status]
        if event.error is not None:
            label = f"{label}: {event.error}"
        details = json.dumps({"Arguments": event.arguments, "Result": event.result, "Error": event.error}, ensure_ascii=False, indent=2, default=str)
        entry = TranscriptEntry("agent", f"MCP {event.server} / {event.tool} — {label}\n\n{details}", EventDetailLevel.NORMAL, cast(McpToolCallTranscriptMetadata, {"type": self.completed_transcript_type}), uuid4())
        return RoutedTranscriptEntry(event.agent_id, event.session_name, entry, identity)

    def render(self, transcript: TranscriptEntry[McpToolCallProgressTranscriptMetadata], context: TranscriptRenderContext) -> Widget:
        return render_text_transcript(transcript, context)

    def render_completed(self, transcript: TranscriptEntry[TranscriptMetadata], context: TranscriptRenderContext) -> Widget:
        title, _, details = transcript.content.partition("\n\n")
        return SourcedTranscript(transcript, context, Collapsible(Static(details or "No additional details were provided.", markup=False), title=title, collapsed=True, classes="completed-activity mcp-activity"))

    def _identity(self, agent_id: UUID, session_name: str, activity_id: str) -> ActivityIdentity:
        return ActivityIdentity(agent_id, session_name, "mcp_tool_call", activity_id)
