"""Help command."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import EventPipeline

from ..models import EventDetailLevel, SystemTranscriptMetadata, TranscriptEntry
from ..session_repository import SessionRepository
from ..transcript_events import TranscriptEntryAdded
from .base import SlashCommand


class HelpCommand(SlashCommand):
    name = "/help"
    _HELP = "/agent ID, /session new [NAME], /session switch NAME, /sessions, /compact, /delete [NAME], /interrupt, /image PATH, /help"

    async def invoke(self, event_pipeline: EventPipeline, agent: Agent[SessionIdentifier], agents: Mapping[UUID, Agent[SessionIdentifier]], session_repository: SessionRepository, argument: str) -> None:
        active = agent.current_session()
        if active is None:
            raise RuntimeError("The selected agent has no active session.")
        entry = TranscriptEntry[SystemTranscriptMetadata]("system", self._HELP, EventDetailLevel.NORMAL, {"type": "system"})
        agent_id = agent.configuration().id
        await session_repository.add_entry(agent_id, active.name, entry)
        event_pipeline.push(TranscriptEntryAdded(agent_id=agent_id, session_name=active.name, entry=entry))
