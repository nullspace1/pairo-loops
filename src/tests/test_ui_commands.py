"""Session command tests."""

import asyncio

from pairo.core.agent import Agent, AgentState, Runner, SandboxMode
from pairo.core.events import EventPipeline, SessionUpdated
from pairo.ui.commands import AgentCommandController
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


class FakeRunner:
    """Small runner fake that keeps session ownership outside Agent."""

    def __init__(self, agent: Agent, pipeline: EventPipeline) -> None:
        self.agents = {agent.id: agent}
        self.pipeline = pipeline
        self._sessions = {agent.id: {"Conversation 1": {"provider": "fake"}}}
        self._current = {agent.id: "Conversation 1"}

    def session_name(self, agent_id) -> str | None:
        return self._current[agent_id]

    def session_names(self, agent_id) -> tuple[str, ...]:
        return tuple(self._sessions[agent_id])

    async def create_session(self, agent_id, session_name: str) -> str:
        self._sessions[agent_id][session_name] = {"provider": "fake"}
        self._current[agent_id] = session_name
        await self.pipeline.publish(
            SessionUpdated(
                agent_id=agent_id,
                session_name=session_name,
                identifier=self._sessions[agent_id][session_name],
            )
        )
        return session_name


def test_default_names_and_session_commands() -> None:
    async def scenario() -> None:
        agent = Agent(
            name="researcher", sandbox=SandboxMode.READ_ONLY, workspace=".", model="test"
        )
        pipeline = EventPipeline()
        runner = FakeRunner(agent, pipeline)
        repository = SqliteSessionRepository()
        await repository.save_session(agent.id, "Conversation 1", {"provider": "fake"})
        controller = AgentCommandController(runner, repository)

        created = await controller.execute(agent.id, "/session new")
        assert created.session_name == "Conversation 2"
        listed = await controller.execute(agent.id, "/sessions")
        assert "Conversation 1" in listed.entry.content
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())
