"""Transcriber-driven transcript widget."""

from collections.abc import Callable, Iterable
from uuid import UUID

from textual import on
from textual.containers import VerticalGroup, VerticalScroll
from textual.widget import Widget

from pairo.core.events import EventPipeline

from ..models import EventDetailLevel, TranscriptEntry, TranscriptMetadata
from ..pairo_event_piper import PairoEvent
from ..transcribers import (
    TranscriptRenderContext,
    TranscriberRegistry,
    render_text_transcript,
)


class TranscriptEntryWidget(VerticalGroup):
    """Stable mounted owner for one durable transcript entry."""

    def __init__(
        self,
        entry: TranscriptEntry[TranscriptMetadata],
        renderer: Callable[[TranscriptEntry[TranscriptMetadata]], Widget],
    ) -> None:
        super().__init__(classes="transcript-entry")
        self._entry_id = entry.entry_id
        self._entry = entry
        self._renderer = renderer
        self._presentation = renderer(entry)

    @property
    def entry_id(self) -> UUID:
        return self._entry_id

    @property
    def entry(self) -> TranscriptEntry[TranscriptMetadata]:
        return self._entry

    def compose(self) -> Iterable[Widget]:
        yield self._presentation

    async def refresh_entry(self, entry: TranscriptEntry[TranscriptMetadata]) -> None:
        """Replace only this entry's presentation while retaining its owner."""
        self._entry = entry
        presentation = self._renderer(entry)
        await self.remove_children()
        self._presentation = presentation
        await self.mount(presentation)
        self.refresh(layout=True)

    def set_visible_at(self, detail_level: EventDetailLevel) -> None:
        visible = (
            detail_level is EventDetailLevel.DEBUG
            or self._entry.detail_level is EventDetailLevel.NORMAL
        )
        self.set_class(not visible, "transcript-entry--hidden")
        self.display = visible


class Transcript(VerticalScroll):
    def __init__(
        self,
        agent_id: UUID,
        session_name: str | None,
        event_pipeline: EventPipeline,
        transcribers: TranscriberRegistry,
        detail_level: EventDetailLevel = EventDetailLevel.NORMAL,
        *,
        agent_name: str = "Agent",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        can_focus: bool | None = None,
        can_focus_children: bool | None = None,
        can_maximize: bool | None = None,
    ) -> None:
        super().__init__(
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            can_focus=can_focus,
            can_focus_children=can_focus_children,
            can_maximize=can_maximize,
        )
        self.entries: list[TranscriptEntry[TranscriptMetadata]] = []
        self.detail_level = detail_level
        self._agent_id = agent_id
        self._session_name = session_name
        self._event_pipeline = event_pipeline
        self._transcribers = transcribers
        self._agent_name = agent_name
        self._live_entry_ids: set[UUID] = set()
        self._entry_widgets: dict[UUID, TranscriptEntryWidget] = {}
        self._has_loaded_history = False
        self._loaded_session_name: str | None = None

    @property
    def visible_entries(self) -> tuple[TranscriptEntry[TranscriptMetadata], ...]:
        return tuple(entry for entry in self.entries if self._is_visible(entry))

    def is_displaying_session(self, session_name: str) -> bool:
        return self._has_loaded_history and self._loaded_session_name == session_name

    async def add_entry(self, entry: TranscriptEntry[TranscriptMetadata]) -> None:
        if entry.entry_id in self._entry_widgets:
            await self.update_entry(entry)
            return
        self.entries.append(entry)
        self._live_entry_ids.add(entry.entry_id)
        await self._mount_entry(entry)

    async def update_entry(self, entry: TranscriptEntry[TranscriptMetadata]) -> None:
        index = self._entry_index_or_none(entry.entry_id)
        if index is None:
            return
        self.entries[index] = entry
        widget = self._entry_widgets.get(entry.entry_id)
        if widget is not None:
            await widget.refresh_entry(entry)
            widget.set_visible_at(self.detail_level)

    async def delete_entry(self, entry_id: UUID) -> None:
        index = self._entry_index_or_none(entry_id)
        if index is None:
            return
        del self.entries[index]
        self._live_entry_ids.discard(entry_id)
        widget = self._entry_widgets.pop(entry_id, None)
        if widget is not None:
            await widget.remove()
        self.refresh(layout=True)

    async def populate_entries(
        self, entries: Iterable[TranscriptEntry[TranscriptMetadata]]
    ) -> None:
        """Populate an empty transcript history without a clearing pass."""
        if self.entries or self._entry_widgets:
            raise RuntimeError("Transcript history may only populate an empty transcript.")
        self._has_loaded_history = True
        self._loaded_session_name = self._session_name
        self.entries = list(entries)
        self._live_entry_ids.clear()
        for entry in self.entries:
            await self._mount_entry(entry)

    async def switch_session(
        self,
        session_name: str,
        entries: Iterable[TranscriptEntry[TranscriptMetadata]],
    ) -> None:
        """Clear only after a loaded session genuinely changes."""
        if not self._has_loaded_history:
            self._session_name = session_name
            await self.populate_entries(entries)
            return
        if self._loaded_session_name == session_name:
            return
        self._session_name = session_name
        self._loaded_session_name = session_name
        self.entries = list(entries)
        self._live_entry_ids.clear()
        self._entry_widgets.clear()
        await self.remove_children()
        for entry in self.entries:
            await self._mount_entry(entry)

    async def set_detail_level(self, detail_level: EventDetailLevel) -> None:
        if self.detail_level == detail_level:
            return
        self.detail_level = detail_level
        for widget in self._entry_widgets.values():
            widget.set_visible_at(detail_level)
        self.refresh(layout=True)

    @on(PairoEvent)
    def relay_pairo_event(self, message: PairoEvent) -> None:
        """Deliver activity updates to mounted cards, not a transcript registry."""
        message.stop()
        for card in self.query(".sourced-transcript"):
            card.post_message(PairoEvent(message.event))

    async def _mount_entry(self, entry: TranscriptEntry[TranscriptMetadata]) -> None:
        widget = TranscriptEntryWidget(entry, self._render_entry)
        self._entry_widgets[entry.entry_id] = widget
        widget.set_visible_at(self.detail_level)
        await self.mount(widget)
        widget.refresh(layout=True)
        self.refresh(layout=True)

    def _render_entry(self, entry: TranscriptEntry[TranscriptMetadata]) -> Widget:
        metadata_type = entry.metadata.get("type", "historical")
        transcriber = self._transcribers.transcript_transcriber_for(metadata_type)
        context = TranscriptRenderContext(
            agent_id=self._agent_id,
            session_name=self._session_name or "",
            event_pipeline=self._event_pipeline,
            mutations=self._transcribers.mutations,
            is_live=entry.entry_id in self._live_entry_ids,
            agent_name=self._agent_name,
        )
        if transcriber is None:
            return render_text_transcript(entry, context)
        return transcriber.render_transcript(entry, context)

    def _is_visible(self, entry: TranscriptEntry[TranscriptMetadata]) -> bool:
        return (
            self.detail_level is EventDetailLevel.DEBUG
            or entry.detail_level is EventDetailLevel.NORMAL
        )

    def _entry_index(self, entry_id: UUID) -> int:
        index = self._entry_index_or_none(entry_id)
        if index is not None:
            return index
        raise LookupError(f"Transcript entry '{entry_id}' is not displayed.")

    def _entry_index_or_none(self, entry_id: UUID) -> int | None:
        for index, entry in enumerate(self.entries):
            if entry.entry_id == entry_id:
                return index
        return None
