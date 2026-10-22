"""Multiline editor which submits only on Ctrl+Enter."""

from dataclasses import dataclass

from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea


class ChatEditor(TextArea):
    """Keep ordinary Enter available for composing a multiline message."""

    BINDINGS = [Binding("ctrl+enter", "submit", "Submit", show=False)]

    @dataclass
    class Submitted(Message):
        """The editor's complete, unmodified text was submitted."""

        editor: "ChatEditor"
        value: str

        @property
        def control(self) -> "ChatEditor":
            return self.editor

    @property
    def value(self) -> str:
        """Compatibility alias for callers transitioning from ``Input``."""
        return self.text

    @value.setter
    def value(self, value: str) -> None:
        self.load_text(value)

    def action_submit(self) -> None:
        self.post_message(self.Submitted(self, self.text))
