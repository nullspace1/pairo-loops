"""Known-session listing command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..commands import CommandOutcome
from ..models import EventDetailLevel, TranscriptEntry
from .base import SlashCommand

if TYPE_CHECKING:
    from ..commands import AgentCommandController


class SessionsCommand(SlashCommand):
    name = "/sessions"

    async def invoke(self, controller: AgentCommandController, agent_name: str, argument: str) -> CommandOutcome:
        sessions = await controller.session_repository.sessions_for(agent_name)
        known = ", ".join(session.session_name for session in sessions)
        current = controller.agents[agent_name].session_name or "none"
        return CommandOutcome(
            TranscriptEntry(
                "system",
                f"Current session: {current}. History: {known or 'none recorded'}.",
                EventDetailLevel.NORMAL,
                {"type": "system"},
            )
        )
