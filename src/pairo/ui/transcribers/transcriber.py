"""Typed registration and routing for event/transcript transcribers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, Protocol, TypeAlias, TypeVar, cast

from pydantic import BaseModel
from textual.widget import Widget

from ..models import (
    ActivityTranscriptMetadata,
    ErrorTranscriptMetadata,
    HistoricalTranscriptMetadata,
    MessageTranscriptMetadata,
    NetworkTranscriptMetadata,
    PermissionRequestTranscriptMetadata,
    PermissionResponseTranscriptMetadata,
    SystemTranscriptMetadata,
    TokenUsageTranscriptMetadata,
    TranscriptEntry,
)
from .routed_transcript_entry import RoutedTranscriptEntry
from .transcript_render_context import TranscriptRenderContext


EventT = TypeVar("EventT", bound=BaseModel)

# A registry contains transcribers with different, concrete metadata types. This
# union preserves every supported shape when that heterogeneity is routed.
TranscriptMetadata: TypeAlias = (
    ActivityTranscriptMetadata
    | ErrorTranscriptMetadata
    | HistoricalTranscriptMetadata
    | MessageTranscriptMetadata
    | NetworkTranscriptMetadata
    | PermissionRequestTranscriptMetadata
    | PermissionResponseTranscriptMetadata
    | SystemTranscriptMetadata
    | TokenUsageTranscriptMetadata
)
MetadataT = TypeVar("MetadataT", bound=TranscriptMetadata)


class Transcriber(ABC, Generic[EventT, MetadataT]):
    """Own conversion and presentation for one event/transcript pairing."""

    def __init__(self, event_type: type[EventT], transcript_type: str) -> None:
        self._event_type = event_type
        self._transcript_type = transcript_type

    @property
    def event_type(self) -> type[EventT]:
        return self._event_type

    @property
    def transcript_type(self) -> str:
        return self._transcript_type

    def write(self, event: BaseModel) -> RoutedTranscriptEntry[MetadataT]:
        """Validate untyped pipeline input before calling the typed writer."""
        if not isinstance(event, self.event_type):
            raise TypeError(
                f"{type(self).__name__} requires {self.event_type.__name__}, "
                f"received {type(event).__name__}."
            )
        return self._write(event)

    @abstractmethod
    def _write(self, event: EventT) -> RoutedTranscriptEntry[MetadataT]: ...

    @abstractmethod
    def render(
        self,
        transcript: TranscriptEntry[MetadataT],
        context: TranscriptRenderContext,
    ) -> Widget: ...


class EventTranscriber(Protocol):
    """A transcriber selected from the event registry."""

    def transcribe(self, event: BaseModel) -> RoutedTranscriptEntry[TranscriptMetadata]: ...


class TranscriptTranscriber(Protocol):
    """A transcriber selected from the transcript registry."""

    def render_transcript(
        self,
        transcript: TranscriptEntry[TranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> Widget: ...


class _RegisteredTranscriber(Generic[EventT, MetadataT]):
    """Contain the type erasure needed to store mixed transcriber types."""

    def __init__(self, transcriber: Transcriber[EventT, MetadataT]) -> None:
        self._transcriber = transcriber

    def transcribe(self, event: BaseModel) -> RoutedTranscriptEntry[TranscriptMetadata]:
        # ``Transcriber.write`` is the runtime validation boundary for events
        # received from the untyped pipeline.
        return cast(RoutedTranscriptEntry[TranscriptMetadata], self._transcriber.write(event))

    def render_transcript(
        self,
        transcript: TranscriptEntry[TranscriptMetadata],
        context: TranscriptRenderContext,
    ) -> Widget:
        # The registry key comes from the entry's metadata type, which was
        # assigned by this transcriber's writer when the entry was created.
        return self._transcriber.render(cast(TranscriptEntry[MetadataT], transcript), context)


class TranscriberRegistry:
    """Register transcribers once and route events and transcript entries safely."""

    def __init__(self) -> None:
        self._event_transcribers: dict[type[BaseModel], EventTranscriber] = {}
        self._transcript_transcribers: dict[str, TranscriptTranscriber] = {}

    def register(self, transcriber: Transcriber[EventT, MetadataT]) -> None:
        """Register one concrete transcriber under both of its routing keys."""
        event_type = transcriber.event_type
        transcript_type = transcriber.transcript_type
        if event_type in self._event_transcribers:
            raise ValueError(
                "Multiple transcribers registered for event "
                f"{event_type.__name__}."
            )
        if transcript_type in self._transcript_transcribers:
            raise ValueError(
                "Multiple transcribers registered for transcript type "
                f"'{transcript_type}'."
            )
        registered = _RegisteredTranscriber(transcriber)
        self._event_transcribers[event_type] = registered
        self._transcript_transcribers[transcript_type] = registered

    @property
    def event_types(self) -> tuple[type[BaseModel], ...]:
        """Event classes currently handled by registered transcribers."""
        return tuple(self._event_transcribers)

    def event_transcriber_for(self, event: BaseModel) -> EventTranscriber:
        """Return the transcriber registered for an incoming event's exact type."""
        return self._event_transcribers[type(event)]

    def transcript_transcriber_for(
        self, transcript_type: str
    ) -> TranscriptTranscriber | None:
        """Return the renderer registered for a transcript metadata type."""
        return self._transcript_transcribers.get(transcript_type)
