"""Help command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..commands import CommandOutcome
from ..models import EventDetailLevel, TranscriptEntry
from .base import SlashCommand

if TYPE_CHECKING:
    from ..commands import AgentCommandController


class HelpCommand(SlashCommand):
    name = "/help"

    async def invoke(self, controller: AgentCommandController, agent_name: str, argument: str) -> CommandOutcome:
        return CommandOutcome(
            TranscriptEntry("system", controller.HELP, EventDetailLevel.NORMAL, {"type": "system"})
        )
