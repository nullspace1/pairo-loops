"""Generation-interruption command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..commands import CommandOutcome
from ..models import EventDetailLevel, TranscriptEntry
from .base import SlashCommand

if TYPE_CHECKING:
    from ..commands import AgentCommandController


class InterruptCommand(SlashCommand):
    name = "/interrupt"

    async def invoke(self, controller: AgentCommandController, agent_name: str, argument: str) -> CommandOutcome:
        interrupted = await controller.agents[agent_name].cancel_generation()
        message = "Interrupt requested." if interrupted else "Agent is already idle."
        return CommandOutcome(
            TranscriptEntry("system", message, EventDetailLevel.NORMAL, {"type": "system"})
        )
