"""Convert completed neutral events into transcript entries."""

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel

from pairo.core.agent import JsonObject
from pairo.core.events import (
    AgentError,
    AgentEvent,
    ClientActivityCompleted,
    MessageSent,
    NetworkMessageSent,
)

from .models import (
    EventDetailLevel,
    TranscriptEntry,
)
from .presentation import CompletedActivityPresenter


@dataclass(frozen=True, slots=True)
class RoutedTranscriptEntry:
    agent_id: UUID
    session_name: str
    entry: TranscriptEntry


class CompletedEventFormatter:
    _NORMAL_ACTIVITY_TYPES = frozenset({"commandExecution", "mcpToolCall"})

    def __init__(self) -> None:
        self._activity_presenter = CompletedActivityPresenter()

    def format(self, event: BaseModel) -> RoutedTranscriptEntry | None:
        if isinstance(event, MessageSent):
            return RoutedTranscriptEntry(
                event.agent_id,
                event.session_name,
                TranscriptEntry(
                    event.source,
                    event.message,
                    EventDetailLevel.NORMAL,
                    {"type": "message"},
                ),
            )
        if isinstance(event, AgentError):
            return RoutedTranscriptEntry(
                event.agent_id,
                event.session_name,
                TranscriptEntry(
                    "system",
                    f"Agent error: {event.message}",
                    EventDetailLevel.NORMAL,
                    {"type": "error"},
                ),
            )
        if isinstance(event, NetworkMessageSent):
            return RoutedTranscriptEntry(
                event.recipient_id,
                event.session_name,
                TranscriptEntry(
                    "network",
                    f"[{event.channel_name}] {event.sender_name}: {event.message}",
                    EventDetailLevel.NORMAL,
                    {"type": "network"},
                ),
            )
        if isinstance(event, AgentEvent) and isinstance(
            event.payload,
            ClientActivityCompleted,
        ):
            presentation = self._activity_presenter.present(event.payload.activity)
            return RoutedTranscriptEntry(
                event.agent_id,
                event.session_name,
                TranscriptEntry(
                    "activity",
                    presentation.text,
                    self._activity_detail_level(event.payload.activity),
                    {
                        "type": "activity",
                        "activity_type": str(event.payload.activity.get("type", "activity")),
                    },
                ),
            )
        return None

    def _activity_detail_level(self, activity: JsonObject) -> EventDetailLevel:
        activity_type = activity.get("type")
        if activity_type in self._NORMAL_ACTIVITY_TYPES:
            return EventDetailLevel.NORMAL
        return EventDetailLevel.DEBUG
