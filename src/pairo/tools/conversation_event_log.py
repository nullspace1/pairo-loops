"""Append future event-pipeline notifications to a shared JSON Lines file."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol, TextIO, cast
from uuid import UUID

from pydantic import BaseModel

from ..core.events.agent_events import AgentActivityCompleted, AgentActivityProgress
from ..core.events.event_pipeline import EventPipeline, Subscription


class ConversationEventLogPolicy(Protocol):
    """Select and order events written by a conversation log."""

    def accept(self, event: BaseModel) -> tuple[BaseModel, ...]: ...

    def drain(self) -> tuple[BaseModel, ...]: ...


class FullFidelityEventLogPolicy:
    """Write every event immediately."""

    def accept(self, event: BaseModel) -> tuple[BaseModel, ...]:
        return (event,)

    def drain(self) -> tuple[BaseModel, ...]:
        return ()


class CoalescingEventLogPolicy:
    """Keep only the latest progress update for each routed activity."""

    def __init__(self) -> None:
        self._progress: dict[tuple[UUID, str, str], AgentActivityProgress] = {}
        self._handlers: dict[
            type[BaseModel], Callable[[BaseModel], tuple[BaseModel, ...]]
        ] = {
            AgentActivityProgress: self._buffer_progress,
            AgentActivityCompleted: self._complete_activity,
        }

    def accept(self, event: BaseModel) -> tuple[BaseModel, ...]:
        handler = self._handlers.get(type(event), self._pass_through)
        return handler(event)

    def drain(self) -> tuple[BaseModel, ...]:
        pending = tuple(self._progress.values())
        self._progress.clear()
        return pending

    def _buffer_progress(self, event: BaseModel) -> tuple[BaseModel, ...]:
        progress = cast(AgentActivityProgress, event)
        self._progress[self._key(progress)] = progress
        return ()

    def _complete_activity(self, event: BaseModel) -> tuple[BaseModel, ...]:
        completed = cast(AgentActivityCompleted, event)
        route = (completed.agent_id, completed.session_name)
        matching = tuple(
            progress
            for key, progress in self._progress.items()
            if key[:2] == route
        )
        for progress in matching:
            del self._progress[self._key(progress)]
        return (*matching, completed)

    def _pass_through(self, event: BaseModel) -> tuple[BaseModel, ...]:
        return (event,)

    def _key(self, event: AgentActivityProgress) -> tuple[UUID, str, str]:
        identity_fields = ("id", "itemId", "turnId", "threadId", "type")
        activity_id = next(
            (
                str(event.activity[field])
                for field in identity_fields
                if field in event.activity
            ),
            "activity",
        )
        return event.agent_id, event.session_name, activity_id


class ConversationEventLog:
    """Persist every future Pydantic pipeline event in one append-only JSONL file.

    The log intentionally observes the event pipeline rather than transcript storage,
    so it captures all event classes without producing pipeline events of its own.
    """

    def __init__(
        self,
        pipeline: EventPipeline,
        output_path: Path,
        policy: ConversationEventLogPolicy | None = None,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO = output_path.open("a", encoding="utf-8")
        self._pipeline = pipeline
        self._policy = policy or FullFidelityEventLogPolicy()
        self._subscription: Subscription[BaseModel] = pipeline.subscribe(
            BaseModel,
            self._append_event,
        )
        self._close_task: asyncio.Task[None] | None = None

    async def flush(self) -> None:
        """Write all events already queued for this log to durable file storage."""
        await self._subscription.wait_until_idle()
        self._append_pending()
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
        for selected in self._policy.accept(event):
            self._write_event(selected)

    def _write_event(self, event: BaseModel) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": type(event).__name__,
            "payload": event.model_dump(mode="json"),
        }
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _append_pending(self) -> None:
        for event in self._policy.drain():
            self._write_event(event)

    async def _close_after_drain(self) -> None:
        await self._subscription.wait_until_idle()
        self._pipeline.unsubscribe(self._subscription)
        self._append_pending()
        try:
            self._flush_file()
        finally:
            self._file.close()

    def _flush_file(self) -> None:
        self._file.flush()
        os.fsync(self._file.fileno())


__all__ = [
    "CoalescingEventLogPolicy",
    "ConversationEventLog",
    "ConversationEventLogPolicy",
    "FullFidelityEventLogPolicy",
]
