"""Shared widget and function for plain-text transcript presentation."""

from rich.text import Text
from textual import events
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static
from typing import TypeVar

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


def render_text_transcript(
    transcript: TranscriptEntry[MetadataT],
    context: TranscriptRenderContext,
) -> Widget:
    del context
    label = Text(
        f"{transcript.role.upper()}  ",
        style=_ROLE_STYLES.get(transcript.role, "bold"),
    )
    return TranscriptMessage(
        Static(Text.assemble(label, transcript.content), markup=False)
    )
