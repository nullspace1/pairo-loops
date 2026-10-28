"""Provider-managed compaction command."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import EventPipeline

from ..models import EventDetailLevel, SystemTranscriptMetadata, TranscriptEntry
from ..session_repository import SessionRepository
from ..transcript_events import AddTranscriptEntry
from .base import SlashCommand


class CompactCommand(SlashCommand):
    name = "/compact"

    async def invoke(self, event_pipeline: EventPipeline, agent: Agent[SessionIdentifier], agents: Mapping[UUID, Agent[SessionIdentifier]], session_repository: SessionRepository, argument: str) -> None:
        if argument:
            raise ValueError("Usage: /compact")
        active = agent.current_session()
        if active is None:
            raise RuntimeError("The selected agent has no active session.")
        await agent.compact()
        entry = TranscriptEntry[SystemTranscriptMetadata]("system", f"Requested compaction for session {active.name}.", EventDetailLevel.NORMAL, {"type": "system"})
        agent_id = agent.configuration().id
        event_pipeline.push(AddTranscriptEntry(agent_id=agent_id, session_name=active.name, entry=entry))
