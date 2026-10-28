"""Session deletion command."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from pairo.core.agent import SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.events import EventPipeline, SessionDeleted

from ..models import EventDetailLevel, SessionMetadata, SystemTranscriptMetadata, TranscriptEntry
from ..session_repository import SessionRepository
from ..transcript_events import AddTranscriptEntry
from .base import SlashCommand


class DeleteCommand(SlashCommand):
    name = "/delete"

    async def invoke(self, event_pipeline: EventPipeline, agent: Agent[SessionIdentifier], agents: Mapping[UUID, Agent[SessionIdentifier]], session_repository: SessionRepository, argument: str) -> None:
        deleted, selected = await self._delete_session(agent, session_repository, argument or None)
        active = agent.current_session()
        if active is None:
            raise RuntimeError("The selected agent has no active session.")
        entry = TranscriptEntry[SystemTranscriptMetadata]("system", f"Deleted session {deleted}. Current session: {selected}.", EventDetailLevel.NORMAL, {"type": "system"})
        agent_id = agent.configuration().id
        event_pipeline.push(SessionDeleted(agent_id=agent_id, session_name=deleted, selected_session_name=selected))
        event_pipeline.push(AddTranscriptEntry(agent_id=agent_id, session_name=active.name, entry=entry))

    async def _delete_session(self, agent: Agent[SessionIdentifier], repository: SessionRepository, session_name: str | None) -> tuple[str, str]:
        agent_id = agent.configuration().id
        active = agent.current_session()
        target = session_name or (active.name if active is not None else None)
        if target is None:
            raise RuntimeError("The selected agent has no active session.")
        persisted = await repository.sessions_for(agent_id)
        if target not in {session.session_name for session in persisted}:
            await repository.get_session(agent_id, target)
        if active is not None and active.name != target:
            selected = active.name
        else:
            remaining = [session for session in persisted if session.session_name != target]
            if remaining:
                selected = await self._select_session(agent, repository, remaining[-1].session_name)
            else:
                selected = await self._create_default_session(agent, repository, persisted)
        await repository.delete_session(agent_id, target)
        return target, selected

    async def _select_session(self, agent: Agent[SessionIdentifier], repository: SessionRepository, session_name: str) -> str:
        metadata = await repository.get_session(agent.configuration().id, session_name)
        active = await agent.activate_session(session_name, metadata.identifier)
        await repository.save_session(agent.configuration().id, active.name, active.identifier.model_dump(mode="json"))
        return active.name

    async def _create_default_session(self, agent: Agent[SessionIdentifier], repository: SessionRepository, persisted: tuple[SessionMetadata, ...]) -> str:
        names = {session.session_name for session in persisted}
        index = 1
        while f"Conversation {index}" in names:
            index += 1
        active = await agent.new_session(f"Conversation {index}")
        await repository.save_session(agent.configuration().id, active.name, active.identifier.model_dump(mode="json"))
        return active.name

