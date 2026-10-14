"""Session command tests."""

import asyncio

from pairo.core.agent import Agent, AgentState, Runner, SandboxMode
from pairo.core.events import EventPipeline, SessionDeleted, SessionUpdated
from pairo.ui.commands import AgentCommandController
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


class FakeRunner:
    """Small runner fake that keeps session ownership outside Agent."""

    def __init__(self, agent: Agent, pipeline: EventPipeline) -> None:
        self.agents = {agent.id: agent}
        self.pipeline = pipeline
        self._sessions = {agent.id: {"Conversation 1": {"provider": "fake"}}}
        self._current = {agent.id: "Conversation 1"}
        self.compacted: list[object] = []

    def session_name(self, agent_id) -> str | None:
        return self._current[agent_id]

    def session_names(self, agent_id) -> tuple[str, ...]:
        return tuple(self._sessions[agent_id])

    async def create_session(self, agent_id, session_name: str) -> str:
        self._sessions[agent_id][session_name] = {"provider": "fake"}
        self._current[agent_id] = session_name
        await self.pipeline.enqueue(
            SessionUpdated(
                agent_id=agent_id,
                session_name=session_name,
                identifier=self._sessions[agent_id][session_name],
            )
        )
        return session_name

    async def delete_session(self, agent_id, session_name: str) -> str | None:
        del self._sessions[agent_id][session_name]
        if self._current[agent_id] == session_name:
            self._current[agent_id] = next(reversed(self._sessions[agent_id]), None)
        return self._current[agent_id]

    async def compact_session(self, agent_id) -> str:
        self.compacted.append(agent_id)
        return self._current[agent_id]


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


def test_compact_and_delete_commands_keep_one_session_available() -> None:
    async def scenario() -> None:
        agent = Agent(
            name="researcher", sandbox=SandboxMode.READ_ONLY, workspace=".", model="test"
        )
        pipeline = EventPipeline()
        runner = FakeRunner(agent, pipeline)
        repository = SqliteSessionRepository()
        await repository.save_session(agent.id, "Conversation 1", {"provider": "fake"})
        controller = AgentCommandController(runner, repository)

        compacted = await controller.execute(agent.id, "/compact")
        assert compacted.session_name == "Conversation 1"
        assert runner.compacted == [agent.id]

        deleted = await controller.execute(agent.id, "/delete")
        assert deleted.session_name == "Conversation 1"
        assert runner.session_names(agent.id) == ("Conversation 1",)
        assert len(await repository.sessions_for(agent.id)) == 1
        assert any(
            isinstance(event, SessionDeleted)
            and event.session_name == "Conversation 1"
            and event.selected_session_name == "Conversation 1"
            for event in pipeline.events
        )

        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())
