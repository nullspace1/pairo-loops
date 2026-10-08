"""Contract implemented by every asynchronous slash command."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..commands import AgentCommandController, CommandOutcome


class SlashCommand(ABC):
    name: str

    @abstractmethod
    async def invoke(
        self,
        controller: AgentCommandController,
        agent_name: str,
        argument: str,
    ) -> CommandOutcome:
        """Execute with the owning controller and parsed argument."""

