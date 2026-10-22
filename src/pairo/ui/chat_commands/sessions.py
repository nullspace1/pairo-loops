"""Known-session listing command."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from ..commands import CommandOutcome
from ..models import (
    EventDetailLevel,
    SessionTableRow,
    SessionTableTranscriptMetadata,
    TranscriptEntry,
)
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
        current = controller.current_session_name(agent_id)
        rows: list[SessionTableRow] = [
            {
                "session_name": session.session_name,
                "current": session.session_name == current,
                "created_at": session.created_at.isoformat(),
                "last_message_at": (
                    session.last_message_at.isoformat()
                    if session.last_message_at is not None
                    else None
                ),
                "last_updated_at": session.last_updated_at.isoformat(),
            }
            for session in sessions
        ]
        return CommandOutcome(
            TranscriptEntry[SessionTableTranscriptMetadata](
                "system",
                "Sessions",
                EventDetailLevel.NORMAL,
                {"type": "session_table", "sessions": rows},
            )
        )

