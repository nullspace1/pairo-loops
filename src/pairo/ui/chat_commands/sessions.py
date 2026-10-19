"""Known-session listing command."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from ..commands import CommandOutcome
from ..models import EventDetailLevel, SystemTranscriptMetadata, TranscriptEntry
from .base import SlashCommand

if TYPE_CHECKING:
    from ..commands import AgentCommandController


class SessionsCommand(SlashCommand):
    name = "/sessions"

    async def invoke(
        self,
        controller: AgentCommandController,
        agent_id: UUID,
        argument: str,
    ) -> CommandOutcome:
        sessions = await controller.session_repository.sessions_for(agent_id)
        known = ", ".join(session.session_name for session in sessions)
        current = controller.current_session_name(agent_id) or "none"
        return CommandOutcome(
            TranscriptEntry[SystemTranscriptMetadata](
                "system",
                f"Current session: {current}. History: {known or 'none recorded'}.",
                EventDetailLevel.NORMAL,
                {"type": "system"},
            )
        )

