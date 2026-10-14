"""Provider-managed compaction command."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from ..commands import CommandOutcome
from ..models import EventDetailLevel, SystemTranscriptMetadata, TranscriptEntry
from .base import SlashCommand

if TYPE_CHECKING:
    from ..commands import AgentCommandController


class CompactCommand(SlashCommand):
    name = "/compact"

    async def invoke(
        self,
        controller: AgentCommandController,
        agent_id: UUID,
        argument: str,
    ) -> CommandOutcome:
        if argument:
            raise ValueError("Usage: /compact")
        session_name = await controller.runner.compact_session(agent_id)
        return CommandOutcome(
            TranscriptEntry[SystemTranscriptMetadata](
                "system",
                f"Requested compaction for session {session_name}.",
                EventDetailLevel.NORMAL,
                {"type": "system"},
            ),
            session_name=session_name,
        )
