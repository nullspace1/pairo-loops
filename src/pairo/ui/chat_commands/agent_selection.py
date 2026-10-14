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
            selected_id = UUID(argument)
        except ValueError as error:
            raise ValueError(f"Invalid agent ID '{argument}'.") from error
        if selected_id not in controller.agents:
            raise ValueError(f"Unknown agent '{argument}'.")
        selected = controller.agents[selected_id]
        return CommandOutcome(
            TranscriptEntry[SystemTranscriptMetadata](
                "system",
                f"Selected agent {selected.name} ({selected.id}).",
                EventDetailLevel.NORMAL,
                {"type": "system"},
            ),
            selected_agent=selected_id,
        )
