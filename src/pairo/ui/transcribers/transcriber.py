"""Combined event-to-transcript and transcript-to-widget contract."""

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeAlias, TypeVar

from pydantic import BaseModel
from textual.widget import Widget

from ..models import TranscriptEntry, TranscriptMetadata
from .routed_transcript_entry import RoutedTranscriptEntry
from .transcript_render_context import TranscriptRenderContext


EventT = TypeVar("EventT", bound=BaseModel)
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


TranscriberList: TypeAlias = list[Transcriber[Any, Any]]
EventTranscriberRegistry: TypeAlias = dict[type[BaseModel], Transcriber[Any, Any]]
TranscriptTranscriberRegistry: TypeAlias = dict[str, Transcriber[Any, Any]]
