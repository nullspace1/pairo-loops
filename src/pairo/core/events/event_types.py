"""Shared asynchronous event types."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol, TypeVar

from pydantic import BaseModel


EventT = TypeVar("EventT", bound=BaseModel)
ListenerEventT = TypeVar("ListenerEventT", bound=BaseModel, contravariant=True)


class EventListener(Protocol[ListenerEventT]):
    def __call__(
        self,
        event: ListenerEventT,
    ) -> BaseModel | None | Awaitable[BaseModel | None]: ...

