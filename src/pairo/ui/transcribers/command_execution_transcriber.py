"""Stateful transcription for every command-execution event."""

from dataclasses import replace
from typing import cast
from uuid import UUID, uuid4

from pairo.core.events import (
    AgentCommandExecutionCompleted,
    AgentCommandExecutionProgress,
    AgentError,
    AgentResponseInterrupted,
)
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from ..models import (
    CommandExecutionProgressTranscriptMetadata,
    CommandExecutionTranscriptMetadata,
    EventDetailLevel,
    TranscriptEntry,
    TranscriptMetadata,
)
from ..pairo_event_piper import on_pairo
from .routed_transcript_entry import ActivityIdentity, RoutedTranscriptEntry
from .text_transcript import SourcedTranscript, render_text_transcript
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class CommandExecutionTranscriber(
    Transcriber[AgentCommandExecutionProgress, CommandExecutionProgressTranscriptMetadata]
):
    """Own command transcript state from the first delta through terminal state."""

    completed_transcript_type = "command_execution"

    def __init__(self) -> None:
        super().__init__(AgentCommandExecutionProgress, "command_execution_progress")
        self._pending: dict[
            ActivityIdentity, TranscriptEntry[CommandExecutionProgressTranscriptMetadata]
        ] = {}

    def _write(
        self, event: AgentCommandExecutionProgress
    ) -> RoutedTranscriptEntry[CommandExecutionProgressTranscriptMetadata]:
        identity = self._identity(event.agent_id, event.session_name, event.activity_id)
        prior = self._pending.get(identity)
        entry = TranscriptEntry(
            "agent",
            f"Command — In progress\n\nLatest output:\n{event.output_delta or 'No output yet.'}",
            EventDetailLevel.DEBUG,
            cast(CommandExecutionProgressTranscriptMetadata, {"type": self.transcript_type}),
            prior.entry_id if prior else uuid4(),
        )
        return RoutedTranscriptEntry(event.agent_id, event.session_name, entry, identity)

    async def persist(
        self, routed: RoutedTranscriptEntry[CommandExecutionProgressTranscriptMetadata]
    ) -> None:
        assert routed.activity is not None and self._mutations is not None
        previous = self._pending.get(routed.activity)
        if previous is None:
            if await self._mutations.add(routed):
                self._pending[routed.activity] = routed.entry
        else:
            await self._mutations.update(routed)
            self._pending[routed.activity] = routed.entry

    @on_pairo(AgentCommandExecutionCompleted)
    async def completed(self, event: AgentCommandExecutionCompleted) -> None:
        identity = self._identity(event.agent_id, event.session_name, event.activity_id)
        previous = self._pending.pop(identity, None)
        if self._mutations is None:
            return
        routed = self.write_completion(event)
        if previous is None:
            await self._mutations.add(routed)
            return
        await self._mutations.update(
            replace(routed, entry=replace(routed.entry, entry_id=previous.entry_id))
        )

    @on_pairo(AgentResponseInterrupted)
    async def interrupted(self, event: AgentResponseInterrupted) -> None:
        await self._terminalize(event.agent_id, event.session_name, "Interrupted")

    @on_pairo(AgentError)
    async def errored(self, event: AgentError) -> None:
        await self._terminalize(event.agent_id, event.session_name, "Stopped after error")

    async def _terminalize(self, agent_id: UUID, session_name: str, label: str) -> None:
        if self._mutations is None:
            return
        matching = [
            (identity, entry)
            for identity, entry in self._pending.items()
            if identity.agent_id == agent_id and identity.session_name == session_name
        ]
        for identity, entry in matching:
            self._pending.pop(identity, None)
            final = replace(
                entry,
                content=entry.content.replace("In progress", label),
                metadata=cast(
                    CommandExecutionProgressTranscriptMetadata,
                    {"type": self.completed_transcript_type},
                ),
            )
            await self._mutations.update(
                RoutedTranscriptEntry(agent_id, session_name, final, identity)
            )

    def write_completion(
        self, event: AgentCommandExecutionCompleted
    ) -> RoutedTranscriptEntry[CommandExecutionTranscriptMetadata]:
        """Build the terminal snapshot owned by this state machine."""
        identity = self._identity(event.agent_id, event.session_name, event.activity_id)
        label = {
            "completed": "Completed successfully",
            "failed": "Failed",
            "declined": "Declined",
            "inProgress": "In progress",
        }[event.status]
        if event.exit_code is not None:
            label = f"{label} (exit code {event.exit_code})"
        entry = TranscriptEntry(
            "agent",
            f"Command — {label}\n\nCommand:\n{event.command}"
            f"\n\nWorking directory: {event.cwd or 'Not provided'}"
            f"\n\nOutput:\n{event.output or 'No output was provided.'}",
            EventDetailLevel.DEBUG,
            cast(CommandExecutionTranscriptMetadata, {"type": self.completed_transcript_type}),
            uuid4(),
        )
        return RoutedTranscriptEntry(event.agent_id, event.session_name, entry, identity)

    def render(
        self,
        transcript: TranscriptEntry[CommandExecutionProgressTranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> Widget:
        return render_text_transcript(transcript, context)

    def render_completed(
        self, transcript: TranscriptEntry[TranscriptMetadata], context: TranscriptRenderContext
    ) -> Widget:
        title, _, details = transcript.content.partition("\n\n")
        return SourcedTranscript(
            transcript,
            context,
            Collapsible(
                Static(details or "No command details were provided.", markup=False),
                title=title,
                collapsed=True,
                classes="completed-activity command-activity",
            ),
        )

    def _identity(self, agent_id: UUID, session_name: str, activity_id: str) -> ActivityIdentity:
        return ActivityIdentity(agent_id, session_name, "command_execution", activity_id)
