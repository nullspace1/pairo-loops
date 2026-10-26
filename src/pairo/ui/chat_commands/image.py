"""Image-publication command."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import EventPipeline, ImageSent

from ..models import EventDetailLevel, MessageTranscriptMetadata, TranscriptEntry
from ..session_repository import SessionRepository
from ..transcript_events import TranscriptEntryAdded
from .base import SlashCommand


class ImageCommand(SlashCommand):
    name = "/image"

    async def invoke(self, event_pipeline: EventPipeline, agent: Agent[SessionIdentifier], agents: Mapping[UUID, Agent[SessionIdentifier]], session_repository: SessionRepository, argument: str) -> None:
        if not argument:
            raise ValueError("Usage: /image PATH")
        active = agent.current_session()
        if active is None:
            raise RuntimeError("The selected agent has no active session.")
        entry = TranscriptEntry[MessageTranscriptMetadata]("user", f"Image: {argument}", EventDetailLevel.NORMAL, {"type": "message"})
        agent_id = agent.configuration().id
        await session_repository.add_entry(agent_id, active.name, entry)
        event_pipeline.push(TranscriptEntryAdded(agent_id=agent_id, session_name=active.name, entry=entry))
        event_pipeline.push(ImageSent(image_path=argument))
