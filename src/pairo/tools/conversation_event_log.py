"""Append future event-pipeline notifications to a shared JSON Lines file."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol, TextIO, cast

from pydantic import BaseModel

from ..core.events.agent_events import (
    AgentTextDelta,
    AgentRecognizedProviderNoOp,
    AgentUnknownProviderEvent,
)
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


class RecognizedProviderNoOpSummary(BaseModel):
    """Cumulative diagnostic count for one known non-UI provider category."""

    category: str
    occurrence_count: int


class UnknownProviderEventSummary(BaseModel):
    """Cumulative diagnostic count for one unknown provider description."""

    description: str
    occurrence_count: int


class _CumulativeCategoryCounter:
    """Retain one representative and report a category only after it changes."""

    def __init__(self) -> None:
        self._occurrences: dict[str, int] = {}
        self._reported_occurrences: dict[str, int] = {}

    def observe(self, category: str) -> bool:
        next_count = self._occurrences.get(category, 0) + 1
        self._occurrences[category] = next_count
        return next_count == 1

    def changed_counts(self) -> tuple[tuple[str, int], ...]:
        changed = tuple(
            (category, count)
            for category, count in self._occurrences.items()
            if self._reported_occurrences.get(category) != count
        )
        self._reported_occurrences.update(dict(changed))
        return changed


class DiagnosticEventLogPolicy:
    """Keep lifecycle evidence while omitting redundant high-volume events."""

    def __init__(self) -> None:
        self._recognized_no_op_counts = _CumulativeCategoryCounter()
        self._unknown_provider_counts = _CumulativeCategoryCounter()
        self._handlers: dict[type[BaseModel], Callable[[BaseModel], tuple[BaseModel, ...]]] = {
            AgentTextDelta: self._discard_text_delta,
            AgentRecognizedProviderNoOp: self._sample_recognized_no_op,
            AgentUnknownProviderEvent: self._sample_unknown_provider_event,
        }

    def accept(self, event: BaseModel) -> tuple[BaseModel, ...]:
        handler = next(
            (handler for event_type, handler in self._handlers.items() if isinstance(event, event_type)),
            self._pass_through,
        )
        return handler(event)

    def drain(self) -> tuple[BaseModel, ...]:
        return (*self._recognized_no_op_summaries(), *self._unknown_provider_summaries())

    def _discard_text_delta(self, _event: BaseModel) -> tuple[BaseModel, ...]:
        return ()

    def _pass_through(self, event: BaseModel) -> tuple[BaseModel, ...]:
        return (event,)

    def _sample_recognized_no_op(self, event: BaseModel) -> tuple[BaseModel, ...]:
        no_op = cast(AgentRecognizedProviderNoOp, event)
        return (no_op,) if self._recognized_no_op_counts.observe(no_op.category) else ()

    def _sample_unknown_provider_event(
        self, event: BaseModel
    ) -> tuple[BaseModel, ...]:
        unknown = cast(AgentUnknownProviderEvent, event)
        return (unknown,) if self._unknown_provider_counts.observe(unknown.description) else ()

    def _recognized_no_op_summaries(self) -> tuple[RecognizedProviderNoOpSummary, ...]:
        return tuple(
            RecognizedProviderNoOpSummary(
                category=category, occurrence_count=count
            )
            for category, count in self._recognized_no_op_counts.changed_counts()
        )

    def _unknown_provider_summaries(self) -> tuple[UnknownProviderEventSummary, ...]:
        return tuple(
            UnknownProviderEventSummary(
                description=description, occurrence_count=count
            )
            for description, count in self._unknown_provider_counts.changed_counts()
        )


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
    "ConversationEventLog",
    "ConversationEventLogPolicy",
    "DiagnosticEventLogPolicy",
    "FullFidelityEventLogPolicy",
    "RecognizedProviderNoOpSummary",
    "UnknownProviderEventSummary",
]
