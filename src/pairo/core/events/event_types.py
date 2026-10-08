"""Shared asynchronous event types."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel


EventT = TypeVar("EventT", bound=BaseModel)
EventListener = Callable[[BaseModel], BaseModel | None | Awaitable[BaseModel | None]]
