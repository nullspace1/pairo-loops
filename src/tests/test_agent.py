"""Public asynchronous Agent contract tests."""

import asyncio
from pathlib import Path

from pairo.core.agent import AgentConfiguration, SandboxMode
from pairo.core.clients import Agent, AgentStatus, FakeAgent, FakeSessionIdentifier
from pairo.core.events import EventPipeline


def test_fake_agent_satisfies_the_runtime_protocol(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        configured = AgentConfiguration(
            name="fake",
            sandbox=SandboxMode.READ_ONLY,
            workspace=tmp_path,
            model="fake",
        )
        agent: Agent[FakeSessionIdentifier] = FakeAgent(configured, pipeline)

        assert agent.configuration() is configured
        assert agent.current_session() is None
        assert agent.status() is AgentStatus.INACTIVE

        active = await agent.new_session(" Conversation 1 ")
        assert active.name == "Conversation 1"
        assert active.identifier.model_dump(mode="json") == {
            "provider": "fake",
            "session_id": active.identifier.session_id,
        }
        assert agent.current_session() == active
        assert agent.status() is AgentStatus.IDLE

        await agent.close()
        assert agent.current_session() is None
        assert agent.status() is AgentStatus.INACTIVE
        await pipeline.close()

    asyncio.run(scenario())
