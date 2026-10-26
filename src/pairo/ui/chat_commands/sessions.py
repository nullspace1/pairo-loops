"""Known-session listing command."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import EventPipeline

from ..models import EventDetailLevel, SessionTableRow, SessionTableTranscriptMetadata, TranscriptEntry
from ..session_repository import SessionRepository
from ..transcript_events import TranscriptEntryAdded
from .base import SlashCommand


class SessionsCommand(SlashCommand):
    name = "/sessions"

    async def invoke(self, event_pipeline: EventPipeline, agent: Agent[SessionIdentifier], agents: Mapping[UUID, Agent[SessionIdentifier]], session_repository: SessionRepository, argument: str) -> None:
        agent_id = agent.configuration().id
        active = agent.current_session()
        if active is None:
            raise RuntimeError("The selected agent has no active session.")
        sessions = await session_repository.sessions_for(agent_id)
        rows: list[SessionTableRow] = [{"session_name": session.session_name, "current": session.session_name == active.name, "created_at": session.created_at.isoformat(), "last_message_at": session.last_message_at.isoformat() if session.last_message_at is not None else None, "last_updated_at": session.last_updated_at.isoformat()} for session in sessions]
        entry = TranscriptEntry[SessionTableTranscriptMetadata]("system", "Sessions", EventDetailLevel.NORMAL, {"type": "session_table", "sessions": rows})
        await session_repository.add_entry(agent_id, active.name, entry)
        event_pipeline.push(TranscriptEntryAdded(agent_id=agent_id, session_name=active.name, entry=entry))
