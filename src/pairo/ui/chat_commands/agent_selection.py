"""Agent-tab selection command."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from ..commands import CommandOutcome
from ..models import EventDetailLevel, SystemTranscriptMetadata, TranscriptEntry
from .base import SlashCommand

if TYPE_CHECKING:
    from ..commands import AgentCommandController


class AgentSelectionCommand(SlashCommand):
    name = "/agent"

    async def invoke(
        self,
        controller: AgentCommandController,
        agent_id: UUID,
        argument: str,
    ) -> CommandOutcome:
        try:
            selected_name = argument
        except ValueError as error:
            raise ValueError(f"Invalid agent name '{argument}'.") from error
        if selected_name not in controller.agents:
            raise ValueError(f"Unknown agent '{argument}'.")
        options = [agent for agent in controller.agents.values() if agent.name == selected_name]
        if len(options) == 0:
            raise ValueError(f"Unknown agent '{argument}'.")
        if len(options) > 1:
            raise ValueError(f"Multiple agents named '{selected_name}'.")
        selected = options[0]
        return CommandOutcome(
            TranscriptEntry[SystemTranscriptMetadata](
                "system",
                f"Selected agent {selected.name} ({selected.id}).",
                EventDetailLevel.NORMAL,
                {"type": "system"},
            ),
            selected_agent=selected.id,
        )
