"""Transcript presentation and durable ownership for live command execution."""
from dataclasses import replace
from typing import cast
from uuid import UUID, uuid4

from pairo.core.events import AgentCommandExecutionCompleted, AgentCommandExecutionProgress, AgentError, AgentResponseInterrupted
from textual.widget import Widget

from ..models import CommandExecutionProgressTranscriptMetadata, EventDetailLevel, TranscriptEntry
from ..pairo_event_piper import on_pairo
from .routed_transcript_entry import ActivityIdentity, RoutedTranscriptEntry
from .text_transcript import render_text_transcript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class CommandExecutionProgressTranscriber(Transcriber[AgentCommandExecutionProgress, CommandExecutionProgressTranscriptMetadata]):
    """Always-active owner of one command family's transient activity state."""
    def __init__(self) -> None:
        super().__init__(AgentCommandExecutionProgress, "command_execution_progress")
        self._pending: dict[ActivityIdentity, TranscriptEntry[CommandExecutionProgressTranscriptMetadata]] = {}

    def _write(self, event: AgentCommandExecutionProgress) -> RoutedTranscriptEntry[CommandExecutionProgressTranscriptMetadata]:
        identity = ActivityIdentity(event.agent_id, event.session_name, "command_execution", event.activity_id)
        prior = self._pending.get(identity)
        entry = TranscriptEntry("agent", f"Command â€” In progress\n\nLatest output:\n{event.output_delta or 'No output yet.'}", EventDetailLevel.NORMAL, cast(CommandExecutionProgressTranscriptMetadata, {"type": "command_execution_progress"}), prior.entry_id if prior else uuid4())
        return RoutedTranscriptEntry(event.agent_id, event.session_name, entry, identity)

    async def persist(self, routed: RoutedTranscriptEntry[CommandExecutionProgressTranscriptMetadata]) -> None:
        assert routed.activity is not None and self._mutations is not None
        prior = self._pending.get(routed.activity)
        self._pending[routed.activity] = routed.entry
        if prior is None: await self._mutations.add(routed)
        else: await self._mutations.update(routed)

    @on_pairo(AgentCommandExecutionCompleted)
    async def completed(self, event: AgentCommandExecutionCompleted) -> None:
        identity = ActivityIdentity(event.agent_id, event.session_name, "command_execution", event.activity_id)
        prior = self._pending.pop(identity, None)
        if self._mutations is None: return
        routed = CommandExecutionCompletedTranscriber().write(event)
        if prior is not None:
            routed = replace(routed, entry=replace(routed.entry, entry_id=prior.entry_id))
            await self._mutations.update(routed)
        else: await self._mutations.add(routed)

    @on_pairo(AgentResponseInterrupted)
    async def interrupted(self, event: AgentResponseInterrupted) -> None:
        await self._terminalize(event.agent_id, event.session_name, "Interrupted")

    @on_pairo(AgentError)
    async def errored(self, event: AgentError) -> None:
        await self._terminalize(event.agent_id, event.session_name, "Stopped after error")

    async def _terminalize(self, agent_id: UUID, session_name: str, label: str) -> None:
        if self._mutations is None: return
        matching = [(identity, entry) for identity, entry in self._pending.items() if identity.agent_id == agent_id and identity.session_name == session_name]
        for identity, entry in matching:
            self._pending.pop(identity, None)
            final = replace(entry, content=entry.content.replace("In progress", label), metadata=cast(CommandExecutionProgressTranscriptMetadata, {"type": "command_execution"}))
            await self._mutations.update(RoutedTranscriptEntry(agent_id, session_name, final, identity))

    def render(self, transcript: TranscriptEntry[CommandExecutionProgressTranscriptMetadata], context: TranscriptRenderContext) -> Widget:
        return render_text_transcript(transcript, context)

from .command_execution_completed_transcriber import CommandExecutionCompletedTranscriber
