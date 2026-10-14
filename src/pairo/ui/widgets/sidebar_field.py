"""One labeled sidebar value."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class SidebarField(Vertical):
    def __init__(self, label: str, value: object) -> None:
        super().__init__(classes="sidebar-field")
        self._label, self._value = label, str(value)

    def compose(self) -> ComposeResult:
        yield Static(self._label, classes="sidebar-field-label")
        yield Static(self._value, classes="sidebar-field-value")
