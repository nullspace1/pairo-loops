"""Agent-tab selection command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..commands import CommandOutcome
from ..models import EventDetailLevel, TranscriptEntry
from .base import SlashCommand

if TYPE_CHECKING:
    from ..commands import AgentCommandController


class AgentSelectionCommand(SlashCommand):
    name = "/agent"

    async def invoke(self, controller: AgentCommandController, agent_name: str, argument: str) -> CommandOutcome:
        if argument not in controller.agents:
            raise ValueError(f"Unknown agent '{argument}'.")
        return CommandOutcome(
            TranscriptEntry(
                "system", f"Selected agent {argument}.", EventDetailLevel.NORMAL, {"type": "system"}
            ),
            selected_agent=argument,
        )
