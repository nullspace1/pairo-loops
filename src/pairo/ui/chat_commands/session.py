"""Session creation and switching commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..commands import CommandOutcome
from ..models import EventDetailLevel, TranscriptEntry
from .base import SlashCommand

if TYPE_CHECKING:
    from ..commands import AgentCommandController


class SessionCommand(SlashCommand):
    name = "/session"

    async def invoke(self, controller: AgentCommandController, agent_name: str, argument: str) -> CommandOutcome:
        agent = controller.agents[agent_name]
        action, _, value = argument.partition(" ")
        value = value.strip()
        if action == "new":
            session_name = value or await controller.default_session_name(agent_name)
            selected = await agent.create_session(session_name)
            return self._selection_outcome("Created", selected)
        if action == "switch" and value:
            selected = await controller.select_session(agent_name, value)
            return self._selection_outcome("Switched to", selected)
        raise ValueError("Usage: /session new [NAME] or /session switch NAME")

    @staticmethod
    def _selection_outcome(action: str, session_name: str) -> CommandOutcome:
        return CommandOutcome(
            TranscriptEntry(
                "system",
                f"{action} session {session_name}.",
                EventDetailLevel.NORMAL,
                {"type": "system"},
            ),
            session_name=session_name,
        )
