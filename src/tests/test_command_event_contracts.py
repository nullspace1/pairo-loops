"""Focused contracts for direct command event publication."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from pairo.core.agent import AgentConfiguration, SandboxPermission, SessionIdentifier
from pairo.core.clients import Agent, FakeAgent
from pairo.core.events import EventPipeline, Subscription
from pairo.ui.chat_commands import HelpCommand, SlashCommand
from pairo.ui.commands import AgentCommandController
from pairo.ui.models import EventDetailLevel, SystemTranscriptMetadata, TranscriptEntry, TranscriptMetadata
from pairo.ui.navigation_events import AgentSwitchRequested, SessionSwitchRequested
from pairo.ui.transcript_events import AddTranscriptEntry


class RecordingEntryRepository:
    def __init__(self, fail_persistence: bool = False) -> None:
        self.fail_persistence = fail_persistence
        self.calls: list[tuple[UUID, str, TranscriptEntry[TranscriptMetadata]]] = []
        self.order: list[str] = []

    async def add_entry(self, agent_id: UUID, session_name: str, entry: TranscriptEntry[TranscriptMetadata]) -> None:
        self.order.append("persist")
        if self.fail_persistence:
            raise OSError("database unavailable")
        self.calls.append((agent_id, session_name, entry))


def configured_agent(tmp_path: Path, pipeline: EventPipeline) -> FakeAgent:
    return FakeAgent(AgentConfiguration(id=uuid4(), name="developer", sandbox=SandboxPermission.READ_ONLY, workspace=tmp_path, model="test"), pipeline, lambda: "done")


def test_navigation_events_are_frozen_and_validate_their_ui_payloads() -> None:
    agent_id = uuid4()
    session_event = SessionSwitchRequested(agent_id=agent_id, session_name="Review")
    assert AgentSwitchRequested(agent_id=agent_id).agent_id == agent_id
    assert session_event.model_dump() == {"agent_id": agent_id, "session_name": "Review"}
    with pytest.raises(ValidationError):
        AgentSwitchRequested(agent_id="not-a-uuid")
    with pytest.raises(ValidationError):
        session_event.session_name = "Other"  # type: ignore[misc]


def test_command_publishes_transcript_request_without_persisting(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = RecordingEntryRepository(fail_persistence=True)
        agent = configured_agent(tmp_path, pipeline)
        active = await agent.new_session("Review")
        controller = AgentCommandController({agent.configuration().id: agent}, pipeline, repository, (HelpCommand(),))
        received: list[AddTranscriptEntry] = []
        subscription = pipeline.subscribe(AddTranscriptEntry, lambda event, _: received.append(event))
        assert await controller.execute(agent.configuration().id, "/help") is None
        await subscription.wait_until_idle()
        assert repository.calls == []
        assert received[0].agent_id == agent.configuration().id
        assert received[0].session_name == active.name
        assert received[0].entry.content == HelpCommand._HELP
        pipeline.unsubscribe(subscription)
        await agent.close()

    asyncio.run(scenario())


def test_controller_has_no_command_event_or_session_helpers() -> None:
    forbidden = {"publish_command_entry", "publish_agent_switch", "publish_session_switch", "create_session", "select_session", "default_session_name", "delete_session", "current_session_name", "event_pipeline", "session_repository", "agents"}
    assert not (forbidden & set(vars(AgentCommandController)))
