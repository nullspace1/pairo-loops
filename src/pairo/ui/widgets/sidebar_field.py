"""One labeled sidebar value."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class SidebarField(Vertical):
    def __init__(self, key: str, label: str, value: object) -> None:
        super().__init__(classes="sidebar-field")
        self.key = key
        self._label, self._value = label, str(value)

    def compose(self) -> ComposeResult:
        yield Static(self._label, classes="sidebar-field-label")
        yield Static(self._value, classes="sidebar-field-value")

    def update(self, label: str, value: object) -> None:
        """Update mounted content without replacing this field widget."""
        normalized = str(value)
        if label != self._label:
            self._label = label
            self.query_one(".sidebar-field-label", Static).update(label)
        if normalized != self._value:
            self._value = normalized
            self.query_one(".sidebar-field-value", Static).update(normalized)
