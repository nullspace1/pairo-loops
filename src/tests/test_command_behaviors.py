"""Behavioral coverage for command parsing and session selection."""

import asyncio
from uuid import UUID, uuid4

import pytest

from pairo.core.agent import Agent, AgentState, SandboxMode
from pairo.core.events import EventPipeline, ImageSent, MessageSent, SessionUpdated
from pairo.ui.commands import AgentCommandController
from pairo.ui.models import EventDetailLevel
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


class CommandRunner:
    """Runner fake exposing the shared-tree operations used by UI commands."""

    def __init__(self, agents: tuple[Agent, ...], pipeline: EventPipeline) -> None:
        self.agents = {agent.id: agent for agent in agents}
        self.pipeline = pipeline
        self.sessions = {agent.id: {"Conversation 1": {"provider": "fake"}} for agent in agents}
        self.current = {agent.id: "Conversation 1" for agent in agents}
        self.sent: list[tuple[UUID, str, str]] = []
        self.interrupted = False

    def session_name(self, agent_id: UUID) -> str | None:
        return self.current[agent_id]

    def session_names(self, agent_id: UUID) -> tuple[str, ...]:
        return tuple(self.sessions[agent_id])

    def state(self, agent_id: UUID) -> AgentState:
        return AgentState.IDLE

    def is_generating(self, agent_id: UUID) -> bool:
        return False

    async def send_message(self, agent_id: UUID, message: str, source: str = "user") -> None:
        self.sent.append((agent_id, message, source))
        await self.pipeline.publish(
            MessageSent(
                agent_id=agent_id,
                session_name=self.current[agent_id],
                message=message,
                source=source,
            )
        )

    async def create_session(self, agent_id: UUID, session_name: str) -> str:
        return await self._load_session(agent_id, session_name, {"provider": "fake"})

    async def restore_session(self, agent_id: UUID, session_name: str, identifier_json) -> str:
        return await self._load_session(agent_id, session_name, identifier_json)

    async def _load_session(self, agent_id: UUID, session_name: str, identifier) -> str:
        self.sessions[agent_id][session_name] = identifier
        self.current[agent_id] = session_name
        await self.pipeline.publish(
            SessionUpdated(
                agent_id=agent_id,
                session_name=session_name,
                identifier=identifier,
            )
        )
        return session_name

    async def switch_session(self, agent_id: UUID, session_name: str) -> str:
        self.current[agent_id] = session_name
        return session_name

    async def interrupt(self, agent_id: UUID) -> bool:
        return self.interrupted


def make_agent(name: str) -> Agent:
    return Agent(
        name=name,
        description="Writes code" if name == "developer" else "",
        sandbox=SandboxMode.READ_ONLY,
        workspace=".",
        model="test",
    )


def test_normal_messages_are_trimmed_and_forwarded() -> None:
    async def scenario() -> None:
        agent = make_agent("developer")
        repository = SqliteSessionRepository()
        pipeline = EventPipeline()
        runner = CommandRunner((agent,), pipeline)
        controller = AgentCommandController(runner, repository)

        outcome = await controller.execute(agent.id, "  hello world  ")
        assert runner.sent == [(agent.id, "hello world", "user")]
        assert outcome.entry is None
        assert outcome.session_name == "Conversation 1"
        with pytest.raises(ValueError, match="Message cannot be empty"):
            await controller.execute(agent.id, "  ")
        with pytest.raises(KeyError):
            await controller.execute(uuid4(), "hello")
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_help_agent_selection_and_unknown_command() -> None:
    async def scenario() -> None:
        developer, reviewer = make_agent("developer"), make_agent("reviewer")
        repository = SqliteSessionRepository()
        pipeline = EventPipeline()
        runner = CommandRunner((developer, reviewer), pipeline)
        controller = AgentCommandController(runner, repository)

        help_outcome = await controller.execute(developer.id, "/help")
        assert help_outcome.entry is not None
        assert help_outcome.entry.content == controller.HELP
        selected = await controller.execute(developer.id, f"/agent {reviewer.id}")
        assert selected.selected_agent == reviewer.id
        with pytest.raises(ValueError, match="Unknown agent"):
            await controller.execute(developer.id, f"/agent {uuid4()}")
        with pytest.raises(ValueError, match="Unknown command"):
            await controller.execute(developer.id, "/nope")
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_session_command_creates_switches_and_restores_sessions() -> None:
    async def scenario() -> None:
        agent = make_agent("developer")
        repository = SqliteSessionRepository()
        pipeline = EventPipeline()
        runner = CommandRunner((agent,), pipeline)
        controller = AgentCommandController(runner, repository)
        await repository.save_session(agent.id, "Conversation 2", {"id": 2})
        await repository.save_session(agent.id, "Conversation 4", {"id": 4})

        created = await controller.execute(agent.id, "/session new")
        assert created.session_name == "Conversation 3"
        loaded = await controller.execute(agent.id, "/session switch Conversation 1")
        assert loaded.session_name == "Conversation 1"
        restored = await controller.execute(agent.id, "/session switch Conversation 4")
        assert restored.session_name == "Conversation 4"
        assert runner.current[agent.id] == "Conversation 4"
        assert runner.sessions[agent.id]["Conversation 4"] == {"id": 4}

        for invalid in ("/session", "/session new-name", "/session switch"):
            with pytest.raises(ValueError, match="Usage"):
                await controller.execute(agent.id, invalid)
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_sessions_interrupt_and_image_commands() -> None:
    async def scenario() -> None:
        agent = make_agent("developer")
        repository = SqliteSessionRepository()
        pipeline = EventPipeline()
        runner = CommandRunner((agent,), pipeline)
        controller = AgentCommandController(runner, repository)

        empty = await controller.execute(agent.id, "/sessions")
        assert empty.entry is not None
        assert "History: none recorded" in empty.entry.content
        idle = await controller.execute(agent.id, "/interrupt")
        assert idle.entry is not None
        assert idle.entry.content == "Agent is already idle."
        runner.interrupted = True
        interrupted = await controller.execute(agent.id, "/interrupt")
        assert interrupted.entry is not None
        assert interrupted.entry.content == "Interrupt requested."

        image = await controller.execute(agent.id, "/image ./diagram.png")
        assert image.entry is not None
        assert image.entry.detail_level is EventDetailLevel.NORMAL
        assert ImageSent(image_path="./diagram.png") in pipeline.events
        with pytest.raises(ValueError, match="Usage"):
            await controller.execute(agent.id, "/image")
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_session_updates_are_persisted_by_the_controller_listener() -> None:
    async def scenario() -> None:
        agent = make_agent("developer")
        repository = SqliteSessionRepository()
        pipeline = EventPipeline()
        runner = CommandRunner((agent,), pipeline)
        AgentCommandController(runner, repository)

        event = SessionUpdated(
            agent_id=agent.id,
            session_name="Conversation 1",
            identifier={"provider": "fake", "revision": 1},
        )
        assert await pipeline.publish(event) is event
        assert (await repository.get_session(agent.id, "Conversation 1")).identifier == {
            "provider": "fake",
            "revision": 1,
        }
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())
