"""Ordered asyncio-native event publication."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections import defaultdict, deque
from collections.abc import Coroutine
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, cast

from pydantic import BaseModel

from .event_lock import EventReceipt, EventSubscription
from .event_types import EventListener, EventT


@dataclass(frozen=True, slots=True)
class _QueuedEvent:
    event: BaseModel
    completion: Future[BaseModel | None]


@dataclass(frozen=True, slots=True)
class _SubmittedEvent:
    event: BaseModel
    completion: Future[BaseModel | None]


ResultT = TypeVar("ResultT")


class _TrackedSubmission(Protocol):
    def cancel(self) -> None: ...

    async def settle(self) -> None: ...


class _SubmittedCoroutine(Generic[ResultT]):
    """Track heterogeneous submitted results through one concrete lifecycle."""

    def __init__(self, future: Future[ResultT]) -> None:
        self._future = future

    def cancel(self) -> None:
        self._future.cancel()

    async def settle(self) -> None:
        await asyncio.wrap_future(self._future)


class PipelineTask(Generic[ResultT]):
    """A coroutine submitted from another thread to the pipeline loop."""

    def __init__(self, future: Future[ResultT]) -> None:
        self._future = future

    def result(self) -> ResultT:
        return self._future.result()

    def cancel(self) -> None:
        self._future.cancel()

    async def wait(self) -> ResultT:
        return await asyncio.wrap_future(self._future)

    def __await__(self):  # type: ignore[no-untyped-def]
        return self.wait().__await__()


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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._submission_lock = threading.Lock()
        self._submissions: deque[_SubmittedEvent] = deque()
        self._drain_scheduled = False
        self._submitted_tasks: set[_TrackedSubmission] = set()
        self._tasks: set[asyncio.Task[object]] = set()
        self._joinable_tasks: set[asyncio.Task[object]] = set()
        self._closed = False

    def bind(self) -> None:
        """Bind all pipeline work to the calling thread's running event loop."""
        loop = asyncio.get_running_loop()
        with self._submission_lock:
            if self._closed:
                raise RuntimeError("Event pipeline is closed.")
            if self._loop is None:
                self._loop = loop
            elif self._loop is not loop:
                raise RuntimeError("Event pipeline is already bound to another event loop.")

    def add_listener(
        self,
        event_type: type[EventT],
        listener: EventListener[EventT],
        *,
        process_existing_events: bool = False,
    ) -> asyncio.Task[None] | None:
        self._require_registration_context()
        self._listeners[event_type].append(
            cast(EventListener[BaseModel], listener)
        )
        if not process_existing_events:
            return None
        existing_events = tuple(
            event
            for event in self.events
            if isinstance(event, event_type)
        )
        if not existing_events:
            return None
        return self.create_task(
            self._process_existing_events(listener, existing_events),
            name="event-pipeline-replay",
        )

    def remove_listener(
        self,
        event_type: type[EventT],
        listener: EventListener[EventT],
    ) -> None:
        self._require_registration_context()
        listeners = self._listeners[event_type]
        stored_listener = cast(EventListener[BaseModel], listener)
        if stored_listener in listeners:
            listeners.remove(stored_listener)

    def listen(self, event_type: type[EventT]) -> EventSubscription[EventT]:
        self._require_pipeline_loop()
        future: asyncio.Future[EventT] = self._bound_loop().create_future()
        self._waiters[event_type].append(future) # type: ignore
        return EventSubscription(future)

    def enqueue(self, event: EventT) -> EventReceipt[EventT]:
        """Accept an event from any thread and route it through the bound loop."""
        completion: Future[BaseModel | None] = Future()
        with self._submission_lock:
            if self._closed:
                raise RuntimeError("Event pipeline is closed.")
            loop = self._loop
            if loop is None:
                raise RuntimeError("Event pipeline is not bound to an event loop.")
            self._submissions.append(_SubmittedEvent(event, completion))
            if not self._drain_scheduled:
                self._drain_scheduled = True
                loop.call_soon_threadsafe(self._drain_submissions)
        return EventReceipt(cast(Future[EventT | None], completion))

    def submit(self, operation: Coroutine[None, None, ResultT]) -> PipelineTask[ResultT]:
        """Submit a coroutine to the bound loop from a synchronous callback."""
        try:
            loop = self._bound_loop()
        except RuntimeError:
            operation.close()
            raise
        try:
            calling_loop = asyncio.get_running_loop()
        except RuntimeError:
            calling_loop = None
        if calling_loop is loop:
            operation.close()
            raise RuntimeError("submit() must be called outside the event pipeline loop.")
        with self._submission_lock:
            if self._closed:
                operation.close()
                raise RuntimeError("Event pipeline is closed.")
            future = asyncio.run_coroutine_threadsafe(operation, loop)
            tracked = _SubmittedCoroutine(future)
            self._submitted_tasks.add(tracked)
        future.add_done_callback(
            lambda _completed: self._discard_submitted_task(tracked)
        )
        return PipelineTask(future)

    def create_task(
        self,
        operation: Coroutine[None, None, ResultT],
        *,
        name: str,
        joinable: bool = True,
    ) -> asyncio.Task[ResultT]:
        """Create a tracked task while already executing on the bound loop."""
        self._require_pipeline_loop()
        task = self._bound_loop().create_task(operation, name=name)
        stored = cast(asyncio.Task[object], task)
        self._tasks.add(stored)
        stored.add_done_callback(self._tasks.discard)
        if joinable:
            self._joinable_tasks.add(stored)
            stored.add_done_callback(self._joinable_tasks.discard)
        return task

    async def join(self) -> None:
        """Wait until every event accepted so far has finished processing."""
        self._require_bound_loop_context()
        while True:
            await asyncio.sleep(0)
            await self._queue.join()
            with self._submission_lock:
                pending_submissions = bool(
                    self._submissions or self._drain_scheduled
                )
            if pending_submissions:
                continue
            current = asyncio.current_task()
            joinable_tasks = tuple(
                task for task in self._joinable_tasks if task is not current
            )
            if not joinable_tasks:
                return
            await asyncio.gather(*joinable_tasks)

    async def close(self) -> None:
        self._require_bound_loop_context()
        with self._submission_lock:
            if self._closed:
                return
            self._closed = True
        with suppress(Exception):
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

        with self._submission_lock:
            submitted_tasks = tuple(self._submitted_tasks)
        for submitted_task in submitted_tasks:
            submitted_task.cancel()
        if submitted_tasks:
            await asyncio.gather(
                *(task.settle() for task in submitted_tasks),
                return_exceptions=True,
            )
        tasks = tuple(self._tasks)
        for pipeline_task in tasks:
            pipeline_task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _drain_submissions(self) -> None:
        # Accepted events must still be moved into the asyncio queue after
        # close() starts rejecting new submissions.
        self._require_bound_loop_context()
        with self._submission_lock:
            submissions = tuple(self._submissions)
            self._submissions.clear()
            self._drain_scheduled = False
        if not submissions:
            return
        self._ensure_worker()
        for submission in submissions:
            self._queue.put_nowait(
                _QueuedEvent(submission.event, submission.completion)
            )

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = self._bound_loop().create_task(
                self._process_queue(),
                name="event-pipeline-worker",
            )

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

    async def _process_existing_events(
        self,
        listener: EventListener[EventT],
        events: tuple[EventT, ...],
    ) -> None:
        """Replay a stable history snapshot without changing stored events."""
        for event in events:
            result = listener(event)
            if inspect.isawaitable(result):
                await result

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Event pipeline is closed.")

    def _discard_submitted_task(self, task: _TrackedSubmission) -> None:
        with self._submission_lock:
            self._submitted_tasks.discard(task)

    def _require_registration_context(self) -> None:
        self._require_open()
        if self._loop is None:
            return
        self._require_bound_loop_context()

    def _bound_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("Event pipeline is not bound to an event loop.")
        return self._loop

    def _require_pipeline_loop(self) -> None:
        self._require_open()
        self._require_bound_loop_context()

    def _require_bound_loop_context(self) -> None:
        if asyncio.get_running_loop() is not self._bound_loop():
            raise RuntimeError("Operation must run on the event pipeline loop.")
