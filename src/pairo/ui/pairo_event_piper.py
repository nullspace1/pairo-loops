"""Bridge Pairo pipeline events into Textual's FIFO message pump."""
from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from inspect import isawaitable
from typing import Callable as TypingCallable, ParamSpec, TypeVar, cast

from pydantic import BaseModel
from textual import on
from textual.message import Message

from pairo.core.events import EventPipeline, Subscription

EventT = TypeVar("EventT", bound=BaseModel)
P = ParamSpec("P")
R = TypeVar("R")


class PairoEvent(Message):
    """A provider-neutral model delivered through Textual's message loop."""

    def __init__(self, event: BaseModel) -> None:
        self.event = event
        super().__init__()


def on_pairo(
    event_type: type[EventT],
    selector: str | None = None,
    **selectors: str,
) -> TypingCallable[[TypingCallable[P, R]], TypingCallable[P, R]]:
    """Handle one concrete Pairo model from a :class:`PairoEvent`.

    Pairo models intentionally carry no Textual selector data.  ``selector`` and
    keyword selectors are accepted for API parity with ``textual.on`` but are
    deliberately ignored; routing is solely by the concrete typed model.
    """
    del selector, selectors

    def decorate(handler: TypingCallable[P, R]) -> TypingCallable[P, R]:
        @wraps(handler)
        async def receive(self: object, message: PairoEvent) -> R | None:
            if not isinstance(message.event, event_type):
                return None
            result = handler(self, cast(EventT, message.event))  # type: ignore[call-arg, arg-type]
            if isawaitable(result):
                return await result
            return result

        return cast(TypingCallable[P, R], on(PairoEvent)(receive))

    return decorate


class TextualEventPiper:
    """Own the one pipeline subscription used to enter a Textual application."""

    def __init__(self, pipeline: EventPipeline, post: Callable[[PairoEvent], bool]) -> None:
        self._pipeline = pipeline
        self._post = post
        self._subscription: Subscription[BaseModel] | None = None
        self._closed = False

    def attach(self) -> None:
        if self._subscription is None and not self._closed:
            self._subscription = self._pipeline.subscribe(BaseModel, self._receive)

    def detach(self) -> None:
        if self._subscription is not None:
            self._pipeline.unsubscribe(self._subscription)
            self._subscription = None
        self._closed = True

    def _receive(self, event: BaseModel, _subscription: Subscription[BaseModel]) -> None:
        if not self._closed:
            self._post(PairoEvent(event))


__all__ = ["PairoEvent", "TextualEventPiper", "on_pairo"]
