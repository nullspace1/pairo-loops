"""Idempotent handles for event-observer registrations."""

from __future__ import annotations

from typing import Generic

from .event_pipeline import EventPipeline
from .event_types import EventListener, EventT


class EventListenerRegistration(Generic[EventT]):
    """Own one listener registration and detach it at most once."""

    def __init__(
        self,
        pipeline: EventPipeline,
        event_type: type[EventT],
        listener: EventListener[EventT],
    ) -> None:
        self._pipeline = pipeline
        self._event_type = event_type
        self._listener = listener
        self._detached = False

    def detach(self) -> None:
        """Stop this observer from receiving future events."""
        if self._detached:
            return
        self._pipeline.remove_listener(self._event_type, self._listener)
        self._detached = True
