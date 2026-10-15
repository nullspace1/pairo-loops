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
            options = [
                agent for agent in controller.agents.values() if agent.name == argument
            ]
            if len(options) == 0:
                raise ValueError(f"Unknown agent '{argument}'.") from error
            if len(options) > 1:
                raise ValueError(f"Multiple agents named '{argument}'.")
            selected = options[0]
        else:
            selected = controller.agents.get(selected_id)
            if selected is None:
                raise ValueError(f"Unknown agent '{argument}'.")
        return CommandOutcome(
            TranscriptEntry[SystemTranscriptMetadata](
                "system",
                f"Selected agent {selected.name} ({selected.id}).",
                EventDetailLevel.NORMAL,
                {"type": "system"},
            ),
            selected_agent=selected.id,
        )
