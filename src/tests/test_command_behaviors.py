"""Focused behavioral coverage for event-driven slash-command dispatch."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from pairo.core.agent import AgentConfiguration, SandboxPermission, SessionIdentifier
from pairo.core.clients import Agent, FakeAgent
from pairo.core.events import EventPipeline, ImageSent, SessionDeleted, Subscription
from pairo.ui import SlashCommand
from pairo.ui.chat_commands import (AgentSelectionCommand, CompactCommand, DeleteCommand,
    HelpCommand, ImageCommand, InterruptCommand, SessionCommand, SessionsCommand)
from pairo.ui.commands import AgentCommandController
from pairo.ui.navigation_events import AgentSwitchRequested, SessionSwitchRequested
from pairo.ui.sqlite_session_repository import SqliteSessionRepository
from pairo.ui.transcript_events import AddTranscriptEntry


def default_commands() -> tuple[SlashCommand, ...]:
    return (HelpCommand(), AgentSelectionCommand(), SessionCommand(), SessionsCommand(),
        CompactCommand(), DeleteCommand(), InterruptCommand(), ImageCommand())


def configured_agent(name: str, tmp_path: Path, pipeline: EventPipeline) -> FakeAgent:
    return FakeAgent(AgentConfiguration(id=uuid4(), name=name, sandbox=SandboxPermission.READ_ONLY,
        workspace=tmp_path, model="test"), pipeline, lambda: "done")


async def save_active_session(agent: FakeAgent, repository: SqliteSessionRepository) -> None:
    active = agent.current_session()
    assert active is not None
    await repository.save_session(agent.configuration().id, active.name,
        active.identifier.model_dump(mode="json"))


def test_execute_returns_none_and_passes_configured_agent_by_identity(tmp_path: Path) -> None:
    class IdentityCommand(SlashCommand):
        name = "/identity"

        def __init__(self) -> None:
            self.received: tuple[EventPipeline, Agent[SessionIdentifier], object, object, str] | None = None

        async def invoke(self, event_pipeline: EventPipeline, agent: Agent[SessionIdentifier], agents, session_repository, argument: str) -> None:
            self.received = (event_pipeline, agent, agents, session_repository, argument)

    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = configured_agent("developer", tmp_path, pipeline)
        await agent.new_session("Conversation 1")
        await save_active_session(agent, repository)
        command = IdentityCommand()
        configured_agents = {agent.configuration().id: agent}
        controller = AgentCommandController(configured_agents, pipeline, repository, (command,))
        assert await controller.execute(agent.configuration().id, "hello") is None  # type: ignore[func-returns-value]
        assert await controller.execute(agent.configuration().id, "/identity value") is None  # type: ignore[func-returns-value]
        assert command.received == (pipeline, agent, configured_agents, repository, "value")
        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_builtin_commands_publish_navigation_transcripts_and_persistence(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        developer = configured_agent("developer", tmp_path, pipeline)
        reviewer = configured_agent("reviewer", tmp_path, pipeline)
        for agent in (developer, reviewer):
            await agent.new_session("Conversation 1")
            await save_active_session(agent, repository)
        developer_id = developer.configuration().id
        controller = AgentCommandController({developer_id: developer,
            reviewer.configuration().id: reviewer}, pipeline, repository, default_commands())
        navigation: list[AgentSwitchRequested | SessionSwitchRequested] = []
        entries: list[AddTranscriptEntry] = []
        images: list[ImageSent] = []

        def record_navigation(event: AgentSwitchRequested | SessionSwitchRequested, _: Subscription) -> None:
            navigation.append(event)

        def record_entry(event: AddTranscriptEntry, _: Subscription) -> None:
            entries.append(event)

        def record_image(event: ImageSent, _: Subscription) -> None:
            images.append(event)

        subscriptions = (pipeline.subscribe(AgentSwitchRequested, record_navigation),
            pipeline.subscribe(SessionSwitchRequested, record_navigation),
            pipeline.subscribe(AddTranscriptEntry, record_entry), pipeline.subscribe(ImageSent, record_image))
        for submitted in ("/help", "/compact", "/interrupt", "/sessions", "/session new Review",
            "/session switch Conversation 1", "/image ./diagram.png",
            f"/agent {reviewer.configuration().id}"):
            assert await controller.execute(developer_id, submitted) is None  # type: ignore[func-returns-value]
        await asyncio.gather(*(subscription.wait_until_idle() for subscription in subscriptions))

        assert images == [ImageSent(image_path="./diagram.png")]
        assert AgentSwitchRequested(agent_id=reviewer.configuration().id) in navigation
        assert SessionSwitchRequested(agent_id=developer_id, session_name="Review") in navigation
        assert SessionSwitchRequested(
            agent_id=developer_id, session_name="Conversation 1"
        ) in navigation
        contents = [event.entry.content for event in entries]
        assert contents == [HelpCommand._HELP, "Requested compaction for session Conversation 1.",
            "Agent is already idle.", "Sessions", "Created session Review.",
            "Switched to session Conversation 1.", "Image: ./diagram.png",
            f"Selected agent reviewer ({reviewer.configuration().id})."]
        assert await repository.entries_for(developer_id, "Review") == ()
        assert await repository.entries_for(developer_id, "Conversation 1") == ()
        await developer.close()
        await reviewer.close()
        await repository.close()

    asyncio.run(scenario())


def test_delete_publishes_replacement_session_and_confirmation(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = configured_agent("developer", tmp_path, pipeline)
        agent_id = agent.configuration().id
        await agent.new_session("Conversation 1")
        await save_active_session(agent, repository)
        controller = AgentCommandController({agent_id: agent}, pipeline, repository, default_commands())
        assert await controller.execute(agent_id, "/session new Review") is None  # type: ignore[func-returns-value]
        deleted: list[SessionDeleted] = []

        def record_deleted(event: SessionDeleted, _: Subscription[SessionDeleted]) -> None:
            deleted.append(event)

        subscription = pipeline.subscribe(SessionDeleted, record_deleted)
        confirmations = []
        requests = pipeline.subscribe(AddTranscriptEntry, lambda event, _: confirmations.append(event))
        assert await controller.execute(agent_id, "/delete Review") is None  # type: ignore[func-returns-value]
        await subscription.wait_until_idle()
        assert deleted == [SessionDeleted(agent_id=agent_id, session_name="Review",
            selected_session_name="Conversation 1")]
        assert [session.session_name for session in await repository.sessions_for(agent_id)] == ["Conversation 1"]
        await requests.wait_until_idle()
        assert [event.entry.content for event in confirmations] == ["Deleted session Review. Current session: Conversation 1."]
        await agent.close()
        await repository.close()

    asyncio.run(scenario())


class DuplicateNameCommand(SlashCommand):
    name = "/duplicate"

    async def invoke(self, event_pipeline: EventPipeline, agent: Agent[SessionIdentifier], agents, session_repository, argument: str) -> None:
        return None


def test_errors_duplicate_names_and_command_snapshot_are_preserved(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = configured_agent("developer", tmp_path, pipeline)
        await agent.new_session("Conversation 1")
        await save_active_session(agent, repository)
        commands = [DuplicateNameCommand()]
        controller = AgentCommandController({agent.configuration().id: agent}, pipeline, repository, commands)
        commands.clear()
        assert await controller.execute(agent.configuration().id, "/duplicate") is None  # type: ignore[func-returns-value]
        with pytest.raises(ValueError, match="Message cannot be empty"):
            await controller.execute(agent.configuration().id, "  ")
        with pytest.raises(KeyError, match="is not configured"):
            await controller.execute(uuid4(), "hello")
        with pytest.raises(ValueError, match="Unknown command"):
            await controller.execute(agent.configuration().id, "/nope")
        with pytest.raises(ValueError, match="Duplicate slash command names: /duplicate"):
            AgentCommandController({agent.configuration().id: agent}, pipeline, repository,
                (DuplicateNameCommand(), DuplicateNameCommand()))
        await agent.close()
        await repository.close()

    asyncio.run(scenario())
