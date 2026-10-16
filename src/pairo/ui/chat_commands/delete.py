"""Session deletion command."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from ..commands import CommandOutcome
from ..models import EventDetailLevel, SystemTranscriptMetadata, TranscriptEntry
from .base import SlashCommand

if TYPE_CHECKING:
    from ..commands import AgentCommandController


class DeleteCommand(SlashCommand):
    name = "/delete"

    async def invoke(
        self,
        controller: AgentCommandController,
        agent_id: UUID,
        argument: str,
    ) -> CommandOutcome:
        deleted, selected = await controller.delete_session(
            agent_id,
            argument or None,
        )
        return CommandOutcome(
            TranscriptEntry[SystemTranscriptMetadata](
                "system",
                f"Deleted session {deleted}. Current session: {selected}.",
                EventDetailLevel.NORMAL,
                {"type": "system"},
            ),
            session_name=selected,
        )

