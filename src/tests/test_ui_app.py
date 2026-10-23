"""Headless smoke tests for the asynchronous Textual integration."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from pairo.core.agent import AgentConfiguration, SandboxPermission
from pairo.core.clients import FakeAgent
from pairo.core.events import (
    AgentCommandExecutionCompleted,
    AgentCommandExecutionProgress,
    EventPipeline,
    MessageSent,
    SessionCreated,
)
from pairo.ui.app import AgentChatApp
from pairo.ui.models import EventDetailLevel, TranscriptEntry
from pairo.ui.sqlite_session_repository import SqliteSessionRepository
from pairo.ui.widgets.agent_sidebar import AgentSidebar
from pairo.ui.widgets.chat_editor import ChatEditor
from textual.widgets import Button, DataTable


def make_agent(
    tmp_path: Path, pipeline: EventPipeline, agent_id: UUID | None = None
) -> FakeAgent:
    return FakeAgent(
        AgentConfiguration(
            id=agent_id or uuid4(),
            name="reviewer",
            sandbox=SandboxPermission.READ_ONLY,
            workspace=tmp_path,
            model="test",
        ),
        pipeline,
        lambda: "approved",
    )


def test_app_initializes_from_active_agent_and_repository(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        active = await agent.new_session("Conversation 1")
        agent_id = agent.configuration().id
        await repository.save_session(
            agent_id,
            active.name,
            active.identifier.model_dump(mode="json"),
        )
        app = AgentChatApp({agent_id: agent}, pipeline, repository)

        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.chats[agent_id].session_name == active.name
            stored = await repository.get_session(agent_id, active.name)
            assert stored.identifier == active.identifier.model_dump(mode="json")

        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_app_rejects_duplicate_agent_names(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        first = make_agent(tmp_path, pipeline)
        second_configuration = first.configuration().model_copy(
            update={
                "description": "different runtime configuration",
            }
        )
        second = FakeAgent(second_configuration, pipeline, lambda: "approved")

        with pytest.raises(ValueError, match="Agent IDs must be unique"):
            AgentChatApp({uuid4(): first, uuid4(): second}, pipeline)

        await first.close()
        await second.close()

    asyncio.run(scenario())


def test_app_restores_latest_session_from_repository(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        agent_id = agent.configuration().id
        first = await agent.new_session("Earlier")
        await repository.save_session(
            agent_id, first.name, first.identifier.model_dump(mode="json")
        )
        latest = await agent.new_session("Latest")
        await repository.save_session(
            agent_id, latest.name, latest.identifier.model_dump(mode="json")
        )
        restored_entry = TranscriptEntry(
            "agent",
            "Stored transcript",
            EventDetailLevel.NORMAL,
            {"type": "message"},
        )
        await repository.add_entry(agent_id, latest.name, restored_entry)
        await agent.close()

        restored_agent = make_agent(tmp_path, pipeline, agent_id)
        assert restored_agent.configuration().id == agent_id
        app = AgentChatApp({agent_id: restored_agent}, pipeline, repository)

        async with app.run_test() as pilot:
            await pilot.pause()
            active = restored_agent.current_session()
            assert active is not None
            assert active.name == latest.name
            assert active.identifier == latest.identifier
            assert app.chats[agent_id].session_name == latest.name
            assert restored_entry in app.chats[agent_id].transcript.entries

        await restored_agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_app_awaits_direct_agent_message_submission(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        active = await agent.new_session("Conversation 1")
        agent_id = agent.configuration().id
        await repository.save_session(
            agent_id,
            active.name,
            active.identifier.model_dump(mode="json"),
        )
        app = AgentChatApp({agent_id: agent}, pipeline, repository)

        async with app.run_test() as pilot:
            chat = app.chats[agent_id]
            chat.input.value = "Review this\ncarefully"
            message_waiter = asyncio.create_task(pipeline.listen(MessageSent))
            await asyncio.sleep(0)
            await pilot.press("ctrl+enter")
            message = await message_waiter
            while not chat.transcript.entries:
                await pilot.pause()

            assert message.agent_id == agent_id
            assert message.message == "Review this\ncarefully"
            assert message.source == "user"
            assert TranscriptEntry(
                "user",
                "Review this\ncarefully",
                EventDetailLevel.NORMAL,
                {"type": "message"},
            ) in chat.transcript.entries
            assert chat.input.text == ""

        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_sidebar_has_information_without_session_controls(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        active = await agent.new_session("Conversation 1")
        agent_id = agent.configuration().id
        await repository.save_session(agent_id, active.name, active.identifier.model_dump(mode="json"))
        app = AgentChatApp({agent_id: agent}, pipeline, repository)
        async with app.run_test():
            sidebar = app.chats[agent_id].sidebar
            assert isinstance(sidebar, AgentSidebar)
            assert not sidebar.query(".session-list")
            assert not sidebar.query(".session-button")
            assert not sidebar.query(Button)
        await agent.close()
        await repository.close()
    asyncio.run(scenario())


def test_sessions_table_is_laid_out_without_resizing_empty_chat(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        active = await agent.new_session("Conversation 1")
        agent_id = agent.configuration().id
        await repository.save_session(
            agent_id, active.name, active.identifier.model_dump(mode="json")
        )
        app = AgentChatApp({agent_id: agent}, pipeline, repository)

        async with app.run_test() as pilot:
            chat = app.chats[agent_id]
            chat.input.text = "/sessions"
            await pilot.press("ctrl+enter")
            for _ in range(5):
                await pilot.pause()

            table = chat.transcript.query_one(DataTable)
            assert table.row_count == 1
            assert table.size.height > 0
            assert table.display
            assert table.visible

        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_chat_editor_keeps_newlines_and_retains_invalid_submission(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        active = await agent.new_session("Conversation 1")
        agent_id = agent.configuration().id
        await repository.save_session(agent_id, active.name, active.identifier.model_dump(mode="json"))
        app = AgentChatApp({agent_id: agent}, pipeline, repository)

        async with app.run_test() as pilot:
            editor = app.chats[agent_id].input
            assert isinstance(editor, ChatEditor)
            editor.text = "first\nsecond"
            await pilot.press("enter")
            assert editor.text.count("\n") == 2
            assert "first\nsecond" in editor.text
            editor.text = "   "
            await pilot.press("ctrl+enter")
            await pilot.pause()
            assert editor.text == "   "

        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_quit_detaches_ui_event_delivery_before_textual_shutdown(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        active = await agent.new_session("Conversation 1")
        agent_id = agent.configuration().id
        await repository.save_session(
            agent_id,
            active.name,
            active.identifier.model_dump(mode="json"),
        )
        app = AgentChatApp({agent_id: agent}, pipeline, repository)

        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._subscriptions

            await app.action_quit()

            assert not app._subscriptions

        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_mounted_transcript_correlates_only_its_live_activity_card(
    tmp_path: Path,
) -> None:
    """A real Textual mount owns activity matching rather than a global ID map."""
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        active = await agent.new_session("Conversation 1")
        agent_id = agent.configuration().id
        await repository.save_session(
            agent_id, active.name, active.identifier.model_dump(mode="json")
        )
        app = AgentChatApp({agent_id: agent}, pipeline, repository)

        async with app.run_test() as pilot:
            pipeline.push(AgentCommandExecutionProgress(
                agent_id=agent_id, session_name=active.name, activity_id="same",
                output_delta="building",
            ))
            for _ in range(3):
                await pilot.pause()
            transcript = app.chats[agent_id].transcript
            assert [entry.content for entry in transcript.entries] == [
                "Command — In progress\n\nLatest output:\nbuilding"
            ]

            pipeline.push(AgentCommandExecutionCompleted(
                agent_id=agent_id, session_name=active.name, activity_id="same",
                command="make", status="completed", output="done",
            ))
            for _ in range(3):
                await pilot.pause()
            assert len(transcript.entries) == 1
            assert "Command — Completed successfully" in transcript.entries[0].content
            assert "Output:\ndone" in transcript.entries[0].content

            pipeline.push(AgentCommandExecutionProgress(
                agent_id=agent_id, session_name="other", activity_id="same",
                output_delta="must not render",
            ))
            for _ in range(3):
                await pilot.pause()
            assert len(transcript.entries) == 1

        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_app_persists_externally_created_session_before_selecting_it(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        initial = await agent.new_session("Conversation 1")
        agent_id = agent.configuration().id
        await repository.save_session(
            agent_id,
            initial.name,
            initial.identifier.model_dump(mode="json"),
        )
        app = AgentChatApp({agent_id: agent}, pipeline, repository)

        async with app.run_test():
            external = await agent.new_session("External")
            event = SessionCreated(
                agent_id=agent_id,
                session_name=external.name,
                identifier=external.identifier.model_dump(mode="json"),
            )

            await app._receive_session_created(event, None)  # type: ignore[arg-type]

            stored = await repository.get_session(agent_id, external.name)
            assert stored.identifier == external.identifier.model_dump(mode="json")
            assert app.chats[agent_id].session_name == external.name

        await agent.close()
        await repository.close()

    asyncio.run(scenario())
