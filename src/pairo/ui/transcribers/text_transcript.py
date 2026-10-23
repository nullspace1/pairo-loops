"""Shared widget and function for plain-text transcript presentation."""

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalGroup, VerticalScroll
from textual.widget import Widget
from textual.widgets import Static
from typing import TypeVar
from uuid import UUID

from ..models import TranscriptEntry
from .transcript_render_context import TranscriptRenderContext


MetadataT = TypeVar("MetadataT")


_ROLE_STYLES = {
    "user": "bold cyan",
    "agent": "bold green",
    "activity": "bold yellow",
    "network": "bold magenta",
    "permission": "bold red",
    "usage": "bold blue",
    "system": "bold bright_black",
}


class TranscriptMessage(VerticalScroll):
    def __init__(self, *children: Widget) -> None:
        super().__init__(*children, classes="transcript-message")

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()


class SourceHeading(Static):
    """A human-readable durable transcript source, never a routing identifier."""

    def __init__(self, transcript: TranscriptEntry[MetadataT], context: TranscriptRenderContext) -> None:
        super().__init__(
            TranscriptSourcePresenter(context.agent_name).present(transcript.role),
            classes="transcript-source",
        )


class TranscriptSourcePresenter:
    """Turn durable source values into labels which are safe to show people.

    ``role`` remains the persisted source column for backwards compatibility.
    The standard historical roles are translated here, while a named network
    sender (or another caller-supplied display label) remains readable.
    """

    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name or "Agent"

    def present(self, source: str) -> str:
        normalized = source.strip()
        labels = {
            "": "System",
            "activity": self._agent_name,
            "agent": self._agent_name,
            "permission": self._agent_name,
            "usage": self._agent_name,
            "system": "System",
            "user": "User",
            # Legacy network rows did not retain a sender display name.
            "network": "Network",
        }
        if normalized in labels:
            return labels[normalized]
        return "System" if self._is_identifier(normalized) else normalized

    def _is_identifier(self, value: str) -> bool:
        try:
            UUID(value)
        except ValueError:
            return False
        return True


class SourcedTranscript(VerticalGroup):
    """Reusable source heading plus arbitrary transcript presentation."""

    def __init__(self, transcript: TranscriptEntry[MetadataT], context: TranscriptRenderContext, content: Widget) -> None:
        super().__init__(classes="sourced-transcript")
        self._transcript = transcript
        self._render_context = context
        self._content = content

    def compose(self) -> ComposeResult:
        yield SourceHeading(self._transcript, self._render_context)
        yield self._content


def render_text_transcript(
    transcript: TranscriptEntry[MetadataT],
    context: TranscriptRenderContext,
) -> Widget:
    label = Text(transcript.content, style=_ROLE_STYLES.get(transcript.role, ""))
    return SourcedTranscript(transcript, context, TranscriptMessage(Static(label, markup=False)))

