"""Headless smoke tests for the asynchronous Textual integration."""

import asyncio
from pathlib import Path

from pairo.core.agent import AgentConfiguration, SandboxMode
from pairo.core.clients import FakeAgent
from pairo.core.events import EventPipeline, MessageSent
from pairo.ui.app import AgentChatApp
from pairo.ui.models import EventDetailLevel, TranscriptEntry
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


def make_agent(tmp_path: Path, pipeline: EventPipeline) -> FakeAgent:
    return FakeAgent(
        AgentConfiguration(
            name="reviewer",
            sandbox=SandboxMode.READ_ONLY,
            workspace=tmp_path,
            model="test",
        ),
        pipeline,
        lambda: "approved",
    )


def test_app_replays_active_session_and_persists_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        active = await agent.new_session("Conversation 1")
        agent_id = agent.configuration().id
        app = AgentChatApp({agent_id: agent}, pipeline, repository)

        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.chats[agent_id].session_name == active.name
            stored = await repository.get_session(agent_id, active.name)
            assert stored.identifier == active.identifier.model_dump(mode="json")

        await agent.close()
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_app_awaits_direct_agent_message_submission(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        await agent.new_session("Conversation 1")
        agent_id = agent.configuration().id
        app = AgentChatApp({agent_id: agent}, pipeline, repository)

        async with app.run_test() as pilot:
            chat = app.chats[agent_id]
            chat.input.value = "Review this"
            await pilot.press("enter")
            await pipeline.join()
            await pilot.pause()

            assert any(
                isinstance(event, MessageSent)
                and event.agent_id == agent_id
                and event.message == "Review this"
                and event.source == "user"
                for event in pipeline.events
            )
            assert TranscriptEntry(
                "user",
                "Review this",
                EventDetailLevel.NORMAL,
                {"type": "message"},
            ) in chat.transcript.entries

        await agent.close()
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())
