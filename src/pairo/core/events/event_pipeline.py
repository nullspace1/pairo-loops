"""Ordered asyncio-native event publication."""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict

from pydantic import BaseModel

from .event_lock import EventSubscription
from .event_types import EventListener, EventT


class EventPipeline:
    """Apply listeners and wake one-shot subscribers in publication order."""

    def __init__(self) -> None:
        self.events: list[BaseModel] = []
        self._listeners: dict[type[BaseModel], list[EventListener]] = defaultdict(list)
        self._waiters: dict[type[BaseModel], list[asyncio.Future[BaseModel]]] = defaultdict(list)
        self._publish_lock = asyncio.Lock()
        self._closed = False

    def add_listener(self, event_type: type[EventT], listener: EventListener) -> None:
        if self._closed:
            raise RuntimeError("Event pipeline is closed.")
        self._listeners[event_type].append(listener)

    def remove_listener(self, event_type: type[EventT], listener: EventListener) -> None:
        listeners = self._listeners[event_type]
        if listener in listeners:
            listeners.remove(listener)

    def listen(self, event_type: type[EventT]) -> EventSubscription[EventT]:
        if self._closed:
            raise RuntimeError("Event pipeline is closed.")
        future: asyncio.Future[EventT] = asyncio.get_running_loop().create_future()
        self._waiters[event_type].append(future) # type: ignore
        return EventSubscription(future)

    async def publish(self, event: EventT) -> EventT | None:
        if self._closed:
            raise RuntimeError("Event pipeline is closed.")
        async with self._publish_lock:
            processed: BaseModel | None = event
            for registered_type, listeners in tuple(self._listeners.items()):
                if processed is None or not isinstance(processed, registered_type):
                    continue
                for listener in tuple(listeners):
                    result = listener(processed)
                    processed = await result if inspect.isawaitable(result) else result
                    if processed is None:
                        return None

            self.events.append(processed)
            for registered_type, waiters in tuple(self._waiters.items()):
                if not isinstance(processed, registered_type):
                    continue
                # Latest subscribers retain first refusal, matching the old pipeline.
                for waiter in reversed(tuple(waiters)):
                    if not waiter.done():
                        waiter.set_result(processed)
                    waiters.remove(waiter)
            return processed  # type: ignore[return-value]

    async def close(self) -> None:
        self._closed = True
        for waiters in self._waiters.values():
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
        self._waiters.clear()
        self._listeners.clear()
