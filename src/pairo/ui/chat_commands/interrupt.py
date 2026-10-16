"""Generation-interruption command."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from ..commands import CommandOutcome
from ..models import EventDetailLevel, SystemTranscriptMetadata, TranscriptEntry
from .base import SlashCommand

if TYPE_CHECKING:
    from ..commands import AgentCommandController


class InterruptCommand(SlashCommand):
    name = "/interrupt"

    async def invoke(
        self,
        controller: AgentCommandController,
        agent_id: UUID,
        argument: str,
    ) -> CommandOutcome:
        interrupted = await controller.runner.interrupt(agent_id)
        message = "Interrupt requested." if interrupted else "Agent is already idle."
        return CommandOutcome(
            TranscriptEntry[SystemTranscriptMetadata](
                "system", message, EventDetailLevel.NORMAL, {"type": "system"}
            )
        )

