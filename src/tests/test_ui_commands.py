"""Session command tests."""

import asyncio

from pairo.core.agent import AgentState
from pairo.core.agent_config import AgentConfiguration, JsonObject, Sandbox
from pairo.core.events import EventPipeline
from pairo.ui.commands import AgentCommandController
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


class FakeAgent:
    def __init__(self) -> None:
        self.name = "researcher"
        self.description = None
        self.session_name = "Conversation 1"
        self.session_names = ("Conversation 1",)
        self.state = AgentState.IDLE
        self.is_generating = False
        self.configuration = AgentConfiguration(
            sandbox=Sandbox.READ_ONLY, workspace=".", model="test"
        )
        self.configured_mcp_servers = ()

    async def send_message(self, message: str, source: str = "user") -> None:
        self.sent = message

    async def create_session(self, session_name: str) -> str:
        self.session_name = session_name
        self.session_names = (*self.session_names, session_name)
        return session_name

    async def restore_session(self, session_name: str, identifier_json: JsonObject) -> str:
        self.session_name = session_name
        self.session_names = (*self.session_names, session_name)
        return session_name

    async def switch_session(self, session_name: str) -> str:
        self.session_name = session_name
        return session_name

    async def cancel_generation(self) -> bool:
        return False


def test_default_names_and_session_commands() -> None:
    async def scenario() -> None:
        agent = FakeAgent()
        repository = SqliteSessionRepository()
        await repository.save_session("researcher", "Conversation 1", {"provider": "fake"})
        controller = AgentCommandController(
            {"researcher": agent},
            EventPipeline(),
            repository,
        )

        created = await controller.execute("researcher", "/session new")
        assert created.session_name == "Conversation 2"
        listed = await controller.execute("researcher", "/sessions")
        assert "Conversation 1" in listed.entry.content
        await repository.close()

    asyncio.run(scenario())
