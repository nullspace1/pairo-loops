"""Event conversion and transcript rendering contracts."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, Protocol, TypeAlias, TypeVar, cast

from pydantic import BaseModel
from textual.widget import Widget

from ..models import TranscriptEntry, TranscriptMetadata
from .routed_transcript_entry import RoutedTranscriptEntry
from .transcript_render_context import TranscriptRenderContext

EventT = TypeVar("EventT", bound=BaseModel)
MetadataT = TypeVar("MetadataT", bound=TranscriptMetadata)


class Transcriber(Generic[EventT, MetadataT]):
    """Convert events and render their transcript entries."""
    def __init__(self, event_type: type[EventT], transcript_type: str) -> None:
        self._event_type = event_type
        self._transcript_type = transcript_type

    @property
    def event_type(self) -> type[EventT]: return self._event_type
    @property
    def transcript_type(self) -> str: return self._transcript_type

    def write(self, event: BaseModel) -> RoutedTranscriptEntry[MetadataT]:
        if not isinstance(event, self.event_type):
            raise TypeError(f"{type(self).__name__} requires {self.event_type.__name__}, received {type(event).__name__}.")
        return self._write(event)

    @abstractmethod
    def _write(self, event: EventT) -> RoutedTranscriptEntry[MetadataT]: ...
    @abstractmethod
    def render(self, transcript: TranscriptEntry[MetadataT], context: TranscriptRenderContext) -> Widget: ...


class EventTranscriber(Protocol):
    def transcribe(self, event: BaseModel) -> RoutedTranscriptEntry[TranscriptMetadata]: ...

class TranscriptTranscriber(Protocol):
    def render_transcript(self, transcript: TranscriptEntry[TranscriptMetadata], context: TranscriptRenderContext) -> Widget: ...

class DurableTranscriptTranscriber(Protocol):
    @property
    def transcript_type(self) -> str: ...
    def render_transcript(self, transcript: TranscriptEntry[TranscriptMetadata], context: TranscriptRenderContext) -> Widget: ...


class _RegisteredTranscriber(Generic[EventT, MetadataT]):
    def __init__(self, transcriber: Transcriber[EventT, MetadataT]) -> None: self._transcriber = transcriber
    def transcribe(self, event: BaseModel) -> RoutedTranscriptEntry[TranscriptMetadata]: return cast(RoutedTranscriptEntry[TranscriptMetadata], self._transcriber.write(event))
    def render_transcript(self, transcript: TranscriptEntry[TranscriptMetadata], context: TranscriptRenderContext) -> Widget: return self._transcriber.render(cast(TranscriptEntry[MetadataT], transcript), context)


class TranscriberRegistry:
    def __init__(self) -> None:
        self._event_transcribers: dict[type[BaseModel], EventTranscriber] = {}
        self._transcript_transcribers: dict[str, TranscriptTranscriber] = {}

    def register(self, transcriber: Transcriber[EventT, MetadataT]) -> None:
        if transcriber.event_type in self._event_transcribers:
            raise ValueError(f"Multiple transcribers registered for event {transcriber.event_type.__name__}.")
        if transcriber.transcript_type in self._transcript_transcribers:
            raise ValueError(f"Multiple transcribers registered for transcript type '{transcriber.transcript_type}'.")
        registered = _RegisteredTranscriber(transcriber)
        self._event_transcribers[cast(type[BaseModel], transcriber.event_type)] = registered
        self._transcript_transcribers[transcriber.transcript_type] = registered

    def register_durable(self, transcriber: DurableTranscriptTranscriber) -> None:
        if transcriber.transcript_type in self._transcript_transcribers: raise ValueError(f"Multiple transcribers registered for transcript type '{transcriber.transcript_type}'.")
        self._transcript_transcribers[transcriber.transcript_type] = transcriber

    @property
    def event_types(self) -> tuple[type[BaseModel], ...]:
        return tuple(self._event_transcribers)
    def has_event_transcriber(self, event: BaseModel) -> bool: return type(event) in self._event_transcribers
    def event_transcriber_for(self, event: BaseModel) -> EventTranscriber: return self._event_transcribers[type(event)]
    def transcript_transcriber_for(self, transcript_type: str) -> TranscriptTranscriber | None: return self._transcript_transcribers.get(transcript_type)
