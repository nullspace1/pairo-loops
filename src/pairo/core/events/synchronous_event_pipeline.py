"""Synchronous cross-thread access to a principal-loop event pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import TypeVar

from .event_pipeline import EventPipeline
from .event_types import EventT


ResultT = TypeVar("ResultT")


class SynchronousEventPipeline:
    """Bridge SDK-owned threads to one principal-loop event pipeline."""

    def __init__(self, event_pipeline: EventPipeline) -> None:
        self._event_pipeline = event_pipeline
        self._loop = asyncio.get_running_loop()
        event_pipeline._require_principal_loop()

    def push(self, event: EventT) -> None:
        """Schedule a fire-and-forget push from any external thread."""
        if self._loop.is_closed():
            raise RuntimeError("The principal event loop is closed.")
        self._loop.call_soon_threadsafe(self._event_pipeline.push, event)

    def run(self, operation: Coroutine[None, None, ResultT]) -> ResultT:
        """Run a coroutine on the principal loop and block the external thread."""
        try:
            calling_loop = asyncio.get_running_loop()
        except RuntimeError:
            calling_loop = None
        if calling_loop is self._loop:
            operation.close()
            raise RuntimeError("run() cannot block the principal event loop.")
        if self._loop.is_closed():
            operation.close()
            raise RuntimeError("The principal event loop is closed.")
        return asyncio.run_coroutine_threadsafe(operation, self._loop).result()


__all__ = ["SynchronousEventPipeline"]
