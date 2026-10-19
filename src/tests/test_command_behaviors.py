"""Behavioral coverage for async command dispatch and repository-owned sessions."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from pairo.core.agent import AgentConfiguration, SandboxPermission
from pairo.core.clients import FakeAgent
from pairo.core.events import EventPipeline, ImageSent, SessionDeleted
from pairo.ui.commands import AgentCommandController
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


def configured_agent(name: str, tmp_path: Path, pipeline: EventPipeline) -> FakeAgent:
    return FakeAgent(
        AgentConfiguration(
            name=name,
            description="Writes code" if name == "developer" else "",
            sandbox=SandboxPermission.READ_ONLY,
            workspace=tmp_path,
            model="test",
        ),
        pipeline,
        lambda: "done",
    )


def test_messages_and_basic_commands_call_agents_directly(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        developer = configured_agent("developer", tmp_path, pipeline)
        reviewer = configured_agent("reviewer", tmp_path, pipeline)
        for agent in (developer, reviewer):
            await agent.new_session("Conversation 1")
        controller = AgentCommandController(
            {
                developer.configuration().id: developer,
                reviewer.configuration().id: reviewer,
            },
            pipeline,
            repository,
        )

        outcome = await controller.execute(
            developer.configuration().id,
            "  hello world  ",
        )
        assert outcome.session_name == "Conversation 1"
        help_outcome = await controller.execute(developer.configuration().id, "/help")
        assert help_outcome.entry.content == controller.HELP
        selected = await controller.execute(
            developer.configuration().id,
            f"/agent {reviewer.configuration().id}",
        )
        assert selected.selected_agent == reviewer.configuration().id

        with pytest.raises(ValueError, match="Message cannot be empty"):
            await controller.execute(developer.configuration().id, "  ")
        with pytest.raises(KeyError):
            await controller.execute(uuid4(), "hello")
        with pytest.raises(ValueError, match="Unknown command"):
            await controller.execute(developer.configuration().id, "/nope")

        await asyncio.gather(developer.close(), reviewer.close())
        await repository.close()

    asyncio.run(scenario())


def test_switch_and_delete_use_repository_as_the_session_catalog(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = configured_agent("developer", tmp_path, pipeline)
        agent_id = agent.configuration().id
        active = await agent.new_session("Conversation 1")
        await repository.save_session(
            agent_id,
            active.name,
            active.identifier.model_dump(mode="json"),
        )
        controller = AgentCommandController({agent_id: agent}, pipeline, repository)

        created = await controller.execute(agent_id, "/session new Review")
        assert created.session_name == "Review"
        assert {item.session_name for item in await repository.sessions_for(agent_id)} == {
            "Conversation 1",
            "Review",
        }

        switched = await controller.execute(agent_id, "/session switch Conversation 1")
        assert switched.session_name == "Conversation 1"
        assert agent.current_session().name == "Conversation 1"

        deletion_waiter = asyncio.create_task(pipeline.listen(SessionDeleted))
        await asyncio.sleep(0)
        deleted = await controller.execute(agent_id, "/delete Conversation 1")
        assert deleted.session_name == "Review"
        assert agent.current_session().name == "Review"
        assert [item.session_name for item in await repository.sessions_for(agent_id)] == [
            "Review"
        ]
        deletion = await deletion_waiter
        assert deletion.selected_session_name == "Review"

        image_waiter = asyncio.create_task(pipeline.listen(ImageSent))
        await asyncio.sleep(0)
        image = await controller.execute(agent_id, "/image ./diagram.png")
        assert image.entry is not None
        assert await image_waiter == ImageSent(image_path="./diagram.png")

        await agent.close()
        await repository.close()

    asyncio.run(scenario())
