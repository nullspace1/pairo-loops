"""Append future event-pipeline notifications to a shared JSON Lines file."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel

from ..events.event_pipeline import EventPipeline, Subscription


class ConversationEventLog:
    """Persist every future Pydantic pipeline event in one append-only JSONL file.

    The log intentionally observes the event pipeline rather than transcript storage,
    so it captures all event classes without producing pipeline events of its own.
    """

    def __init__(self, pipeline: EventPipeline, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO = output_path.open("a", encoding="utf-8")
        self._pipeline = pipeline
        self._subscription: Subscription[BaseModel] = pipeline.subscribe(
            BaseModel,
            self._append_event,
        )
        self._close_task: asyncio.Task[None] | None = None

    async def flush(self) -> None:
        """Write all events already queued for this log to durable file storage."""
        await self._subscription.wait_until_idle()
        close_task = self._close_task
        if close_task is not None:
            # Closing owns the final flush and closes the file immediately after it.
            # Joining that task keeps flush's durability contract without writing to a
            # file which the close task may already have closed.
            await close_task
            return
        self._flush_file()

    async def close(self) -> None:
        """Drain this log, detach it, and close its file exactly once."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._close_after_drain(),
                name="conversation-event-log-close",
            )
        await self._close_task

    def _append_event(
        self,
        event: BaseModel,
        _subscription: Subscription[BaseModel],
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": type(event).__name__,
            "payload": event.model_dump(mode="json"),
        }
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def _close_after_drain(self) -> None:
        await self._subscription.wait_until_idle()
        self._pipeline.unsubscribe(self._subscription)
        try:
            self._flush_file()
        finally:
            self._file.close()

    def _flush_file(self) -> None:
        self._file.flush()
        os.fsync(self._file.fileno())


__all__ = ["ConversationEventLog"]
