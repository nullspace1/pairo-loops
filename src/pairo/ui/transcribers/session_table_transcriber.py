"""Render durable session-list transcript entries as Textual tables."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Static
from typing import cast

from ..models import (
    SessionTableRow,
    SessionTableTranscriptMetadata,
    TranscriptEntry,
    TranscriptMetadata,
)
from .transcript_render_context import TranscriptRenderContext


class SessionTableWidget(Vertical):
    """A compact, non-interactive view of the sessions known to one agent."""

    def __init__(self, sessions: tuple[SessionTableRow, ...]) -> None:
        super().__init__(classes="session-table-message")
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        if not self._sessions:
            yield Static("No sessions recorded.", classes="empty-session-table")
        table = DataTable[str](show_row_labels=False, cursor_type="none")
        table.add_columns("Session", "Current", "Created", "Last message", "Updated")
        for session in self._sessions:
            table.add_row(
                session["session_name"],
                "Current" if session["current"] else "",
                session["created_at"],
                session["last_message_at"] or "—",
                session["last_updated_at"],
            )
        yield table


class SessionTableTranscriber:
    """Own table rendering for JSON-persisted ``/sessions`` command results."""

    transcript_type = "session_table"

    def render_transcript(
        self,
        transcript: TranscriptEntry[TranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> Widget:
        del context
        metadata = cast(SessionTableTranscriptMetadata, transcript.metadata)
        return SessionTableWidget(tuple(metadata["sessions"]))
