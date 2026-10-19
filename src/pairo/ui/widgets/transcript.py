"""Transcriber-driven transcript widget."""

from collections.abc import Iterable
from uuid import UUID

from textual.containers import VerticalScroll
from textual.events import MouseScrollDown, MouseScrollUp

from pairo.core.events import EventPipeline

from ..models import EventDetailLevel, TranscriptEntry, TranscriptMetadata
from ..transcript_service import TranscriptService
from ..transcribers import (
    TranscriptRenderContext,
    TranscriberRegistry,
    render_text_transcript,
)


class Transcript(VerticalScroll):
    def __init__(
        self,
        agent_id: UUID,
        session_name: str | None,
        event_pipeline: EventPipeline,
        transcript_service: TranscriptService,
        transcribers: TranscriberRegistry,
        detail_level: EventDetailLevel = EventDetailLevel.NORMAL,
        *,
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
        self._transcript_service = transcript_service
        self._transcribers = transcribers
        self._live_entry_ids: set[UUID] = set()
        self._render_generation = 0

    @property
    def visible_entries(self) -> tuple[TranscriptEntry[TranscriptMetadata], ...]:
        return tuple(entry for entry in self.entries if self._is_visible(entry))

    async def add_entry(self, entry: TranscriptEntry[TranscriptMetadata]) -> None:
        self.entries.append(entry)
        self._live_entry_ids.add(entry.entry_id)
        if self._is_visible(entry):
            await self._mount_entry(entry, scroll_after_mount=True)

    async def update_entry(self, entry: TranscriptEntry[TranscriptMetadata]) -> None:
        index = self._entry_index(entry.entry_id)
        previous = self.entries[index]
        self.entries[index] = entry
        # The permission card updates itself before publishing the durable entry.
        # Removing the card from its active button handler can deadlock Textual.
        if (
            previous.metadata.get("type") == "permission_request"
            and entry.metadata.get("type") == "permission_request"
        ):
            return
        await self._render_all()

    async def delete_entry(self, entry_id: UUID) -> None:
        index = self._entry_index(entry_id)
        del self.entries[index]
        self._live_entry_ids.discard(entry_id)
        await self._render_all()

    async def replace_entries(self, entries: Iterable[TranscriptEntry[TranscriptMetadata]]) -> None:
        self.entries = list(entries)
        self._live_entry_ids.clear()
        await self._render_all()

    async def set_detail_level(self, detail_level: EventDetailLevel) -> None:
        if self.detail_level != detail_level:
            self.detail_level = detail_level
            await self._render_all()

    def set_session(self, session_name: str) -> None:
        self._session_name = session_name

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        event.prevent_default()

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        event.prevent_default()

    async def _render_all(self) -> None:
        self._render_generation += 1
        await self.remove_children()
        for entry in self.visible_entries:
            await self._mount_entry(entry, scroll_after_mount=False)
        self._scroll_to_latest_after_layout()

    async def _mount_entry(self, entry: TranscriptEntry[TranscriptMetadata], *, scroll_after_mount: bool) -> None:
        metadata_type = entry.metadata.get("type", "historical")
        transcriber = self._transcribers.transcript_transcriber_for(metadata_type)
        context = TranscriptRenderContext(
            agent_id=self._agent_id,
            session_name=self._session_name or "",
            event_pipeline=self._event_pipeline,
            transcript_service=self._transcript_service,
            is_live=entry.entry_id in self._live_entry_ids,
        )
        widget = (
            render_text_transcript(entry, context)
            if transcriber is None
            else transcriber.render_transcript(entry, context)
        )
        await self.mount(widget)
        if scroll_after_mount:
            self._scroll_to_latest_after_layout()

    def _scroll_to_latest_after_layout(self) -> None:
        generation = self._render_generation
        self.call_after_refresh(
            lambda: self.scroll_end(animate=False)
            if generation == self._render_generation
            else None
        )

    def _is_visible(self, entry: TranscriptEntry[TranscriptMetadata]) -> bool:
        return (
            self.detail_level is EventDetailLevel.DEBUG
            or entry.detail_level is EventDetailLevel.NORMAL
        )

    def _entry_index(self, entry_id: UUID) -> int:
        for index, entry in enumerate(self.entries):
            if entry.entry_id == entry_id:
                return index
        raise LookupError(f"Transcript entry '{entry_id}' is not displayed.")
