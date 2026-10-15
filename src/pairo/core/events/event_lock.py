"""Backward-free asynchronous one-shot event subscription."""

from __future__ import annotations

import asyncio
from typing import Generic

from .event_types import EventT


class EventSubscription(Generic[EventT]):
    def __init__(self, future: asyncio.Future[EventT]) -> None:
        self._future = future

    async def wait(self) -> EventT:
        return await self._future

    def cancel(self) -> None:
        self._future.cancel()

    def __await__(self):  # type: ignore[no-untyped-def]
        return self.wait().__await__()


class EventReceipt(Generic[EventT]):
    """Completion handle for one event accepted by the pipeline."""

    def __init__(self, future: asyncio.Future[EventT | None]) -> None:
        self._future = future

    async def wait(self) -> EventT | None:
        return await self._future

    def __await__(self):  # type: ignore[no-untyped-def]
        return self.wait().__await__()
