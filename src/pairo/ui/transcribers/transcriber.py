"""Stateful Textual message-pump transcription and rendering contracts."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, Protocol, TypeAlias, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel
from textual import on
from textual.message import Message
from textual.message_pump import MessagePump
from textual.widget import Widget

from ..models import TranscriptEntry, TranscriptMetadata
from ..pairo_event_piper import PairoEvent
from ..session_repository import SessionNotFoundError, SessionRepository
from pairo.core.events import EventPipeline
from ..transcript_events import TranscriptEntryAdded, TranscriptEntryDeleted, TranscriptEntryUpdated
from .routed_transcript_entry import RoutedTranscriptEntry
from .transcript_render_context import TranscriptRenderContext

EventT = TypeVar("EventT", bound=BaseModel)
MetadataT = TypeVar("MetadataT", bound=TranscriptMetadata)


class TranscriptionRequested(Message):
    def __init__(self, event: BaseModel) -> None:
        self.event = event
        super().__init__()


class TranscriptMutations:
    """A transcriber-local commit-then-notify boundary."""
    def __init__(self, repository: SessionRepository, pipeline: EventPipeline) -> None:
        self._repository = repository
        self._pipeline = pipeline

    async def add(self, routed: RoutedTranscriptEntry[MetadataT]) -> bool:
        try:
            await self._repository.add_entry(routed.agent_id, routed.session_name, routed.entry)
        except SessionNotFoundError:
            return False
        self._pipeline.push(TranscriptEntryAdded(agent_id=routed.agent_id, session_name=routed.session_name, entry=routed.entry))
        return True

    async def update(self, routed: RoutedTranscriptEntry[MetadataT]) -> None:
        await self._repository.update_entry(routed.agent_id, routed.session_name, routed.entry)
        self._pipeline.push(TranscriptEntryUpdated(agent_id=routed.agent_id, session_name=routed.session_name, entry=routed.entry))

    async def delete(self, agent_id: UUID, session_name: str, entry_id: UUID) -> None:
        await self._repository.delete_entry(agent_id, session_name, entry_id)
        self._pipeline.push(TranscriptEntryDeleted(agent_id=agent_id, session_name=session_name, entry_id=entry_id))


class Transcriber(MessagePump, Generic[EventT, MetadataT]):
    """One conversion/rendering owner with a private Textual request queue."""
    def __init__(self, event_type: type[EventT], transcript_type: str) -> None:
        super().__init__()
        self._event_type = event_type
        self._transcript_type = transcript_type
        self._mutations: TranscriptMutations | None = None

    @property
    def event_type(self) -> type[EventT]: return self._event_type
    @property
    def transcript_type(self) -> str: return self._transcript_type

    def bind(self, mutations: TranscriptMutations, parent: MessagePump) -> None:
        if self._mutations is not None:
            raise RuntimeError("A transcriber may only be bound once.")
        self._mutations = mutations
        self._parent = parent
        self._start_messages()

    async def shutdown(self) -> None:
        await self._close_messages()

    def write(self, event: BaseModel) -> RoutedTranscriptEntry[MetadataT]:
        if not isinstance(event, self.event_type):
            raise TypeError(f"{type(self).__name__} requires {self.event_type.__name__}, received {type(event).__name__}.")
        return self._write(event)

    def request(self, event: BaseModel) -> bool:
        return self.post_message(TranscriptionRequested(event))

    @on(TranscriptionRequested)
    async def transcribe_request(self, message: TranscriptionRequested) -> None:
        if not isinstance(message.event, self.event_type):
            return
        if self._mutations is None:
            return
        await self.persist(self.write(message.event))

    async def persist(self, routed: RoutedTranscriptEntry[MetadataT]) -> None:
        assert self._mutations is not None
        await self._mutations.add(cast(RoutedTranscriptEntry[TranscriptMetadata], routed))

    @abstractmethod
    def _write(self, event: EventT) -> RoutedTranscriptEntry[MetadataT]: ...
    @abstractmethod
    def render(self, transcript: TranscriptEntry[MetadataT], context: TranscriptRenderContext) -> Widget: ...


class EventTranscriber(Protocol):
    def transcribe(self, event: BaseModel) -> RoutedTranscriptEntry[TranscriptMetadata]: ...
    def request(self, event: BaseModel) -> bool: ...

class TranscriptTranscriber(Protocol):
    def render_transcript(self, transcript: TranscriptEntry[TranscriptMetadata], context: TranscriptRenderContext) -> Widget: ...

class DurableTranscriptTranscriber(Protocol):
    @property
    def transcript_type(self) -> str: ...
    def render_transcript(self, transcript: TranscriptEntry[TranscriptMetadata], context: TranscriptRenderContext) -> Widget: ...


class _RegisteredTranscriber(Generic[EventT, MetadataT]):
    def __init__(self, transcriber: Transcriber[EventT, MetadataT]) -> None: self._transcriber = transcriber
    def transcribe(self, event: BaseModel) -> RoutedTranscriptEntry[TranscriptMetadata]: return cast(RoutedTranscriptEntry[TranscriptMetadata], self._transcriber.write(event))
    def request(self, event: BaseModel) -> bool: return self._transcriber.request(event)
    def render_transcript(self, transcript: TranscriptEntry[TranscriptMetadata], context: TranscriptRenderContext) -> Widget: return self._transcriber.render(cast(TranscriptEntry[MetadataT], transcript), context)


class TranscriberRegistry:
    def __init__(self) -> None:
        self._event_transcribers: dict[type[BaseModel], EventTranscriber] = {}
        self._transcript_transcribers: dict[str, TranscriptTranscriber] = {}
        self._pumps: list[Transcriber[BaseModel, TranscriptMetadata]] = []
        self._started = False
        self._mutation_publisher: TranscriptMutations | None = None

    def register(self, transcriber: Transcriber[EventT, MetadataT]) -> None:
        if transcriber.event_type in self._event_transcribers:
            raise ValueError(f"Multiple transcribers registered for event {transcriber.event_type.__name__}.")
        if transcriber.transcript_type in self._transcript_transcribers:
            raise ValueError(f"Multiple transcribers registered for transcript type '{transcriber.transcript_type}'.")
        registered = _RegisteredTranscriber(transcriber)
        self._event_transcribers[cast(type[BaseModel], transcriber.event_type)] = registered
        self._transcript_transcribers[transcriber.transcript_type] = registered
        self._pumps.append(cast(Transcriber[BaseModel, TranscriptMetadata], transcriber))

    def register_durable(self, transcriber: DurableTranscriptTranscriber) -> None:
        if transcriber.transcript_type in self._transcript_transcribers: raise ValueError(f"Multiple transcribers registered for transcript type '{transcriber.transcript_type}'.")
        self._transcript_transcribers[transcriber.transcript_type] = transcriber

    async def start(self, repository: SessionRepository, pipeline: EventPipeline, parent: MessagePump) -> None:
        if self._started: return
        mutations = TranscriptMutations(repository, pipeline)
        self._mutation_publisher = mutations
        for pump in self._pumps: pump.bind(mutations, parent)
        self._started = True

    async def shutdown(self) -> None:
        if not self._started: return
        for pump in self._pumps: await pump.shutdown()
        self._started = False
        self._mutation_publisher = None

    @property
    def mutations(self) -> TranscriptMutations | None: return self._mutation_publisher
    @property
    def event_types(self) -> tuple[type[BaseModel], ...]:
        return tuple(self._event_transcribers)
    def has_event_transcriber(self, event: BaseModel) -> bool: return type(event) in self._event_transcribers
    def deliver(self, event: BaseModel) -> None:
        """Broadcast raw events to always-active state owners."""
        for pump in self._pumps: pump.post_message(PairoEvent(event))
    def event_transcriber_for(self, event: BaseModel) -> EventTranscriber: return self._event_transcribers[type(event)]
    def transcript_transcriber_for(self, transcript_type: str) -> TranscriptTranscriber | None: return self._transcript_transcribers.get(transcript_type)
