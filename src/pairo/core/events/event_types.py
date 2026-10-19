"""Shared event type variables."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel


EventT = TypeVar("EventT", bound=BaseModel)

