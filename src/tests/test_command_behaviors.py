"""Behavioral coverage for command parsing and session selection."""

import asyncio

import pytest

from pairo.core.agent import AgentState
from pairo.core.agent_config import AgentConfiguration, JsonObject, Sandbox
from pairo.core.events import EventPipeline, ImageSent, SessionUpdated
from pairo.ui.commands import AgentCommandController
from pairo.ui.models import EventDetailLevel
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


class CommandAgent:
    def __init__(self, name: str = "developer") -> None:
        self.name = name
        self.description = "Writes code"
        self.session_name: str | None = "Conversation 1"
        self.session_names = ("Conversation 1",)
        self.state = AgentState.IDLE
        self.is_generating = False
        self.configuration = AgentConfiguration(
            sandbox=Sandbox.READ_ONLY,
            workspace=".",
            model="test",
        )
        self.configured_mcp_servers = ()
        self.calls: list[tuple[object, ...]] = []
        self.interrupted = False

    async def send_message(self, message: str, source: str = "user") -> None:
        self.calls.append(("send", message, source))

    async def create_session(self, session_name: str) -> str:
        self.calls.append(("create", session_name))
        self.session_name = session_name
        self.session_names = (*self.session_names, session_name)
        return session_name

    async def restore_session(self, session_name: str, identifier_json: JsonObject) -> str:
        self.calls.append(("restore", session_name, identifier_json))
        self.session_name = session_name
        self.session_names = (*self.session_names, session_name)
        return session_name

    async def switch_session(self, session_name: str) -> str:
        self.calls.append(("switch", session_name))
        self.session_name = session_name
        return session_name

    async def cancel_generation(self) -> bool:
        self.calls.append(("interrupt",))
        return self.interrupted


def test_normal_messages_are_trimmed_and_forwarded() -> None:
    async def scenario() -> None:
        agent = CommandAgent()
        repository = SqliteSessionRepository()
        pipeline = EventPipeline()
        controller = AgentCommandController({agent.name: agent}, pipeline, repository)

        outcome = await controller.execute(agent.name, "  hello world  ")
        assert agent.calls == [("send", "hello world", "user")]
        assert outcome.entry is None
        assert outcome.session_name == "Conversation 1"
        with pytest.raises(ValueError, match="Message cannot be empty"):
            await controller.execute(agent.name, "  ")
        with pytest.raises(KeyError):
            await controller.execute("missing", "hello")
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_help_agent_selection_and_unknown_command() -> None:
    async def scenario() -> None:
        developer = CommandAgent("developer")
        reviewer = CommandAgent("reviewer")
        repository = SqliteSessionRepository()
        pipeline = EventPipeline()
        controller = AgentCommandController(
            {agent.name: agent for agent in (developer, reviewer)},
            pipeline,
            repository,
        )

        help_outcome = await controller.execute("developer", "/help")
        assert help_outcome.entry is not None
        assert help_outcome.entry.content == controller.HELP
        selected = await controller.execute("developer", "/agent reviewer")
        assert selected.selected_agent == "reviewer"
        with pytest.raises(ValueError, match="Unknown agent"):
            await controller.execute("developer", "/agent missing")
        with pytest.raises(ValueError, match="Unknown command"):
            await controller.execute("developer", "/nope")
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_session_command_creates_switches_and_restores_sessions() -> None:
    async def scenario() -> None:
        agent = CommandAgent()
        repository = SqliteSessionRepository()
        pipeline = EventPipeline()
        controller = AgentCommandController({agent.name: agent}, pipeline, repository)
        await repository.save_session("developer", "Conversation 2", {"id": 2})
        await repository.save_session("developer", "Conversation 4", {"id": 4})

        created = await controller.execute("developer", "/session new")
        assert created.session_name == "Conversation 3"
        loaded = await controller.execute("developer", "/session switch Conversation 1")
        assert loaded.session_name == "Conversation 1"
        restored = await controller.execute("developer", "/session switch Conversation 4")
        assert restored.session_name == "Conversation 4"
        assert ("switch", "Conversation 1") in agent.calls
        assert ("restore", "Conversation 4", {"id": 4}) in agent.calls

        for invalid in ("/session", "/session new-name", "/session switch"):
            with pytest.raises(ValueError, match="Usage"):
                await controller.execute("developer", invalid)
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_sessions_interrupt_and_image_commands() -> None:
    async def scenario() -> None:
        agent = CommandAgent()
        repository = SqliteSessionRepository()
        pipeline = EventPipeline()
        controller = AgentCommandController({agent.name: agent}, pipeline, repository)

        empty = await controller.execute("developer", "/sessions")
        assert empty.entry is not None
        assert "History: none recorded" in empty.entry.content
        idle = await controller.execute("developer", "/interrupt")
        assert idle.entry is not None
        assert idle.entry.content == "Agent is already idle."
        agent.interrupted = True
        interrupted = await controller.execute("developer", "/interrupt")
        assert interrupted.entry is not None
        assert interrupted.entry.content == "Interrupt requested."

        image = await controller.execute("developer", "/image ./diagram.png")
        assert image.entry is not None
        assert image.entry.detail_level is EventDetailLevel.NORMAL
        assert ImageSent(image_path="./diagram.png") in pipeline.events
        with pytest.raises(ValueError, match="Usage"):
            await controller.execute("developer", "/image")
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_session_updates_are_persisted_by_the_controller_listener() -> None:
    async def scenario() -> None:
        agent = CommandAgent()
        repository = SqliteSessionRepository()
        pipeline = EventPipeline()
        AgentCommandController({agent.name: agent}, pipeline, repository)

        event = SessionUpdated(
            agent_name="developer",
            session_name="Conversation 1",
            identifier={"provider": "fake", "revision": 1},
        )
        assert await pipeline.publish(event) is event
        assert (await repository.get_session("developer", "Conversation 1")).identifier == {
            "provider": "fake",
            "revision": 1,
        }
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())
