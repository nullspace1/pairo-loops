"""Agent-tab selection command."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import EventPipeline

from ..models import EventDetailLevel, SystemTranscriptMetadata, TranscriptEntry
from ..navigation_events import AgentSwitchRequested
from ..session_repository import SessionRepository
from ..transcript_events import TranscriptEntryAdded
from .base import SlashCommand


class AgentSelectionCommand(SlashCommand):
    name = "/agent"

    async def invoke(self, event_pipeline: EventPipeline, agent: Agent[SessionIdentifier], agents: Mapping[UUID, Agent[SessionIdentifier]], session_repository: SessionRepository, argument: str) -> None:
        selected = self._selected_agent(agents, argument)
        active = agent.current_session()
        if active is None:
            raise RuntimeError("The selected agent has no active session.")
        entry = TranscriptEntry[SystemTranscriptMetadata]("system", f"Selected agent {selected.configuration().name} ({selected.configuration().id}).", EventDetailLevel.NORMAL, {"type": "system"})
        agent_id = agent.configuration().id
        await session_repository.add_entry(agent_id, active.name, entry)
        event_pipeline.push(TranscriptEntryAdded(agent_id=agent_id, session_name=active.name, entry=entry))
        event_pipeline.push(AgentSwitchRequested(agent_id=selected.configuration().id))

    def _selected_agent(self, agents: Mapping[UUID, Agent[SessionIdentifier]], argument: str) -> Agent[SessionIdentifier]:
        try:
            selected_id = UUID(argument)
        except ValueError as error:
            options = [configured for configured in agents.values() if configured.configuration().name == argument]
            if not options:
                raise ValueError(f"Unknown agent '{argument}'.") from error
            if len(options) > 1:
                raise ValueError(f"Multiple agents named '{argument}'.")
            return options[0]
        selected = agents.get(selected_id)
        if selected is None:
            raise ValueError(f"Unknown agent '{argument}'.")
        return selected
