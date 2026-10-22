"""Transcribe and render completed provider activities."""

from pairo.core.events import AgentActivityCompleted
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from ..models import ActivityTranscriptMetadata, EventDetailLevel, TranscriptEntry
from ..presentation import CompletedActivityPresenter
from .routed_transcript_entry import RoutedTranscriptEntry
from .transcriber import Transcriber
from .transcript_render_context import TranscriptRenderContext


class ActivityCompletedTranscriber(
    Transcriber[AgentActivityCompleted, ActivityTranscriptMetadata]
):
    _NORMAL_ACTIVITY_TYPES = frozenset({"commandExecution", "mcpToolCall"})

    def __init__(self) -> None:
        super().__init__(AgentActivityCompleted, "activity")
        self._presenter = CompletedActivityPresenter()

    def _write(
        self, event: AgentActivityCompleted
    ) -> RoutedTranscriptEntry[ActivityTranscriptMetadata]:
        presentation = self._presenter.present(event.activity)
        activity_type = str(event.activity.get("type", "activity"))
        detail_level = (
            EventDetailLevel.NORMAL
            if activity_type in self._NORMAL_ACTIVITY_TYPES
            else EventDetailLevel.DEBUG
        )
        return RoutedTranscriptEntry(
            event.agent_id,
            event.session_name,
            TranscriptEntry(
                "activity",
                presentation.text,
                detail_level,
                {"type": "activity", "activity_type": activity_type},
            ),
        )

    def render(
        self,
        transcript: TranscriptEntry[ActivityTranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> Widget:
        del context
        title, _, details = transcript.content.partition("\n\n")
        return Collapsible(
            Static(details or "No additional details were provided.", markup=False),
            title=title,
            collapsed=True,
            classes="completed-activity",
        )

