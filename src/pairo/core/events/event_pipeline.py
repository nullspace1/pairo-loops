"""Ordered asyncio-native event publication."""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel

from .event_lock import EventReceipt, EventSubscription
from .event_types import EventListener, EventT


@dataclass(frozen=True, slots=True)
class _QueuedEvent:
    event: BaseModel
    completion: asyncio.Future[BaseModel | None]


class EventPipeline:
    """Apply listeners and wake one-shot subscribers in publication order."""

    def __init__(self) -> None:
        self.events: list[BaseModel] = []
        self._listeners: dict[
            type[BaseModel], list[EventListener[BaseModel]]
        ] = defaultdict(list)
        self._waiters: dict[type[BaseModel], list[asyncio.Future[BaseModel]]] = defaultdict(list)
        self._queue: asyncio.Queue[_QueuedEvent] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

    def add_listener(
        self,
        event_type: type[EventT],
        listener: EventListener[EventT],
        *,
        process_existing_events: bool = False,
    ) -> asyncio.Task[None] | None:
        if self._closed:
            raise RuntimeError("Event pipeline is closed.")
        self._listeners[event_type].append(
            cast(EventListener[BaseModel], listener)
        )
        if not process_existing_events:
            return None
        existing_events = tuple(
            cast(EventT, event)
            for event in self.events
            if isinstance(event, event_type)
        )
        if not existing_events:
            return None
        return asyncio.create_task(
            self._process_existing_events(listener, existing_events)
        )

    def remove_listener(
        self,
        event_type: type[EventT],
        listener: EventListener[EventT],
    ) -> None:
        listeners = self._listeners[event_type]
        stored_listener = cast(EventListener[BaseModel], listener)
        if stored_listener in listeners:
            listeners.remove(stored_listener)

    def listen(self, event_type: type[EventT]) -> EventSubscription[EventT]:
        if self._closed:
            raise RuntimeError("Event pipeline is closed.")
        future: asyncio.Future[EventT] = asyncio.get_running_loop().create_future()
        self._waiters[event_type].append(future) # type: ignore
        return EventSubscription(future)

    def enqueue(self, event: EventT) -> EventReceipt[EventT]:
        """Accept an event for ordered processing and return its completion receipt."""
        if self._closed:
            raise RuntimeError("Event pipeline is closed.")
        loop = asyncio.get_running_loop()
        completion: asyncio.Future[BaseModel | None] = loop.create_future()
        # Fire-and-forget callers should not produce an unobserved-future warning.
        # Awaiting the receipt still re-raises the stored exception.
        completion.add_done_callback(
            lambda future: future.exception() if not future.cancelled() else None
        )
        self._ensure_worker()
        self._queue.put_nowait(_QueuedEvent(event, completion))
        return EventReceipt(cast(asyncio.Future[EventT | None], completion))

    async def join(self) -> None:
        """Wait until every event accepted so far has finished processing."""
        await self._queue.join()

    async def close(self) -> None:
        self._closed = True
        await self.join()
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        for waiters in self._waiters.values():
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
        self._waiters.clear()
        self._listeners.clear()

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._process_queue())

    async def _process_queue(self) -> None:
        while True:
            queued = await self._queue.get()
            try:
                processed = await self._process_event(queued.event)
            except Exception as error:
                if not queued.completion.done():
                    queued.completion.set_exception(error)
            else:
                if not queued.completion.done():
                    queued.completion.set_result(processed)
            finally:
                self._queue.task_done()

    async def _process_event(self, event: BaseModel) -> BaseModel | None:
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
            for waiter in reversed(tuple(waiters)):
                if not waiter.done():
                    waiter.set_result(processed)
                waiters.remove(waiter)
        return processed

    @staticmethod
    async def _process_existing_events(
        listener: EventListener[EventT],
        events: tuple[EventT, ...],
    ) -> None:
        """Replay a stable history snapshot without changing stored events."""
        for event in events:
            result = listener(event)
            if inspect.isawaitable(result):
                await result
