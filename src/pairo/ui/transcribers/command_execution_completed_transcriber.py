"""Transcript presentation for completed commands."""

from uuid import uuid4

from pairo.core.events import AgentCommandExecutionCompleted

from textual.widget import Widget

from textual.widgets import Collapsible, Static

from ..models import CommandExecutionTranscriptMetadata, EventDetailLevel, TranscriptEntry

from .routed_transcript_entry import ActivityIdentity, RoutedTranscriptEntry

from .text_transcript import SourcedTranscript

from .transcriber import Transcriber

from .transcript_render_context import TranscriptRenderContext

class CommandExecutionCompletedTranscriber(Transcriber[AgentCommandExecutionCompleted, CommandExecutionTranscriptMetadata]):

    def __init__(self) -> None: super().__init__(AgentCommandExecutionCompleted, "command_execution")

    def request(self, event: object) -> bool:

        """The paired live owner consumes completion from the broadcast."""

        return False



    def _write(self, event: AgentCommandExecutionCompleted) -> RoutedTranscriptEntry[CommandExecutionTranscriptMetadata]:

        identity=ActivityIdentity(event.agent_id,event.session_name,"command_execution",event.activity_id)

        label={"completed":"Completed successfully","failed":"Failed","declined":"Declined","inProgress":"In progress"}[event.status]

        if event.exit_code is not None: label=f"{label} (exit code {event.exit_code})"

        return RoutedTranscriptEntry(event.agent_id,event.session_name,TranscriptEntry("agent",f"Command â€” {label}\n\nCommand:\n{event.command}\n\nWorking directory: {event.cwd or 'Not provided'}\n\nOutput:\n{event.output or 'No output was provided.'}",EventDetailLevel.NORMAL,{"type":"command_execution"},uuid4()),identity)

    def render(self, transcript: TranscriptEntry[CommandExecutionTranscriptMetadata], context: TranscriptRenderContext) -> Widget:

        title,_,details=transcript.content.partition("\n\n"); return SourcedTranscript(transcript,context,Collapsible(Static(details or "No command details were provided.",markup=False),title=title,collapsed=True,classes="completed-activity command-activity"))
