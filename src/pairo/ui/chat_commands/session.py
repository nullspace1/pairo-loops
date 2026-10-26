"""Session creation and switching commands."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import EventPipeline

from ..models import EventDetailLevel, SystemTranscriptMetadata, TranscriptEntry
from ..navigation_events import SessionSwitchRequested
from ..session_repository import SessionRepository
from ..transcript_events import TranscriptEntryAdded
from .base import SlashCommand


class SessionCommand(SlashCommand):
    name = "/session"

    async def invoke(self, event_pipeline: EventPipeline, agent: Agent[SessionIdentifier], agents: Mapping[UUID, Agent[SessionIdentifier]], session_repository: SessionRepository, argument: str) -> None:
        action, _, value = argument.partition(" ")
        value = value.strip()
        if action == "new":
            session_name = value or await self._default_session_name(agent, session_repository)
            selected = await self._create_session(agent, session_repository, session_name)
            await self._publish_selection(event_pipeline, agent, session_repository, "Created", selected)
            return
        if action == "switch" and value:
            selected = await self._select_session(agent, session_repository, value)
            await self._publish_selection(event_pipeline, agent, session_repository, "Switched to", selected)
            return
        raise ValueError("Usage: /session new [NAME] or /session switch NAME")

    async def _create_session(self, agent: Agent[SessionIdentifier], repository: SessionRepository, session_name: str) -> str:
        active = await agent.new_session(session_name)
        await repository.save_session(agent.configuration().id, active.name, active.identifier.model_dump(mode="json"))
        return active.name

    async def _select_session(self, agent: Agent[SessionIdentifier], repository: SessionRepository, session_name: str) -> str:
        active = agent.current_session()
        if active is not None and active.name == session_name:
            return active.name
        metadata = await repository.get_session(agent.configuration().id, session_name)
        active = await agent.activate_session(session_name, metadata.identifier)
        await repository.save_session(agent.configuration().id, active.name, active.identifier.model_dump(mode="json"))
        return active.name

    async def _default_session_name(self, agent: Agent[SessionIdentifier], repository: SessionRepository) -> str:
        names = {session.session_name for session in await repository.sessions_for(agent.configuration().id)}
        index = 1
        while f"Conversation {index}" in names:
            index += 1
        return f"Conversation {index}"

    async def _publish_selection(self, event_pipeline: EventPipeline, agent: Agent[SessionIdentifier], repository: SessionRepository, action: str, session_name: str) -> None:
        active = agent.current_session()
        if active is None:
            raise RuntimeError("The selected agent has no active session.")
        entry = TranscriptEntry[SystemTranscriptMetadata]("system", f"{action} session {session_name}.", EventDetailLevel.NORMAL, {"type": "system"})
        agent_id = agent.configuration().id
        await repository.add_entry(agent_id, active.name, entry)
        event_pipeline.push(SessionSwitchRequested(agent_id=agent_id, session_name=active.name))
        event_pipeline.push(TranscriptEntryAdded(agent_id=agent_id, session_name=active.name, entry=entry))
