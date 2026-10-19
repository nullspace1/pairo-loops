"""Principal-loop event broadcasting through independent subscriptions."""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Generic, TypeAlias, cast

from pydantic import BaseModel

from .event_types import EventT


class _SubscriptionOwner:
    """Distinguish subscriptions created by different pipelines."""


class _QueueStop:
    """Wake an unsubscribed delivery task without using an event value."""


_STOP = _QueueStop()


class Subscription(Generic[EventT]):
    """Deliver one event type to one callback in buffered FIFO order."""

    def __init__(
        self,
        owner: _SubscriptionOwner,
        loop: asyncio.AbstractEventLoop,
        event_type: type[EventT],
        callback: EventCallback[EventT],
    ) -> None:
        self._owner = owner
        self._loop = loop
        self._event_type = event_type
        self._callback = callback
        self._queue: asyncio.Queue[EventT | _QueueStop] = asyncio.Queue()
        self._cancelled = asyncio.Event()
        self._active = True
        self._task = loop.create_task(
            self._deliver(),
            name=f"event-subscription-{event_type.__name__}",
        )

    @property
    def event_type(self) -> type[EventT]:
        return self._event_type

    @property
    def active(self) -> bool:
        return self._active

    async def wait_until_cancelled(self) -> None:
        """Wait until this subscription has been removed from its pipeline."""
        await self._cancelled.wait()

    def _push(self, event: EventT) -> None:
        if self._active:
            self._queue.put_nowait(event)

    def _unsubscribe(self) -> None:
        if not self._active:
            return
        self._active = False
        self._cancelled.set()
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._queue.task_done()
        self._queue.put_nowait(_STOP)

    async def _deliver(self) -> None:
        while True:
            queued = await self._queue.get()
            try:
                if queued is _STOP:
                    return
                if not self._active:
                    continue
                event = cast(EventT, queued)
                try:
                    result = self._callback(event, self)
                    if inspect.isawaitable(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._loop.call_exception_handler(
                        {
                            "message": "Event subscription callback failed",
                            "exception": error,
                            "subscription": self,
                            "event": event,
                        }
                    )
            finally:
                self._queue.task_done()


EventCallback: TypeAlias = Callable[
    [EventT, Subscription[EventT]],
    object | Awaitable[object],
]


class EventPipeline:
    """Broadcast transient events on the loop where the pipeline was created."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._subscription_owner = _SubscriptionOwner()
        self._subscriptions: dict[
            type[BaseModel], list[Subscription[BaseModel]]
        ] = defaultdict(list)

    async def listen(self, event_type: type[EventT]) -> EventT:
        """Wait for the next matching event through a temporary subscription."""
        self._require_principal_loop()
        received: asyncio.Future[EventT] = self._loop.create_future()

        def receive(
            event: EventT,
            subscription: Subscription[EventT],
        ) -> None:
            if not received.done():
                received.set_result(event)
            self.unsubscribe(subscription)

        subscription = self.subscribe(event_type, receive)
        try:
            return await received
        finally:
            self.unsubscribe(subscription)

    def subscribe(
        self,
        event_type: type[EventT],
        callback: EventCallback[EventT],
    ) -> Subscription[EventT]:
        """Subscribe one callback to all future matching events."""
        self._require_principal_loop()
        subscription = Subscription(
            self._subscription_owner,
            self._loop,
            event_type,
            callback,
        )
        self._subscriptions[event_type].append(
            cast(Subscription[BaseModel], subscription)
        )
        return subscription

    def unsubscribe(self, subscription: Subscription[EventT]) -> None:
        """Detach one subscription, finishing only its current callback."""
        self._require_principal_loop()
        if subscription._owner is not self._subscription_owner:
            raise ValueError("Subscription does not belong to this event pipeline.")
        subscriptions = self._subscriptions.get(subscription.event_type)
        stored = cast(Subscription[BaseModel], subscription)
        if subscriptions is None or stored not in subscriptions:
            if not subscription.active:
                return
            raise ValueError("Subscription does not belong to this event pipeline.")
        subscriptions.remove(stored)
        if not subscriptions:
            self._subscriptions.pop(subscription.event_type, None)
        subscription._unsubscribe()

    def push(self, event: EventT) -> None:
        """Buffer an event for every matching subscription and return immediately."""
        self._require_principal_loop()
        for event_type, subscriptions in tuple(self._subscriptions.items()):
            if not event_type.__instancecheck__(event):
                continue
            for subscription in tuple(subscriptions):
                subscription._push(event)

    def _require_principal_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as error:
            raise RuntimeError(
                "Event pipeline operations require its principal event loop."
            ) from error
        if loop is not self._loop:
            raise RuntimeError(
                "Event pipeline operations require its principal event loop."
            )


__all__ = ["EventCallback", "EventPipeline", "Subscription"]
