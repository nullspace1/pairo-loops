"""Contract implemented by every asynchronous slash command."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import EventPipeline

if TYPE_CHECKING:
    from ..session_repository import SessionRepository


class SlashCommand(ABC):
    name: str

    @abstractmethod
    async def invoke(
        self,
        event_pipeline: EventPipeline,
        agent: Agent[SessionIdentifier],
        agents: Mapping[UUID, Agent[SessionIdentifier]],
        session_repository: SessionRepository,
        argument: str,
    ) -> None:
        """Execute with the complete UI command context and parsed argument."""


