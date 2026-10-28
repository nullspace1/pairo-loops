"""Headless smoke tests for the asynchronous Textual integration."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from pairo.core.agent import AgentConfiguration, SandboxPermission
from pairo.core.clients import AgentStatus, FakeAgent
from pairo.core.events import (
    AgentCommandExecutionCompleted,
    AgentMcpToolCallCompleted,
    EventPipeline,
    MessageSent,
    SessionCreated,
)
from pairo.ui.app import AgentChatApp
from pairo.ui.chat_commands import (
    AgentSelectionCommand, CompactCommand, DeleteCommand, HelpCommand, ImageCommand,
    InterruptCommand, SessionCommand, SessionsCommand,
)
from pairo.ui.transcribers import default_transcribers
from pairo.ui.widgets.sidebar_statistics import (
    UpdateSessionStatistic,
    create_default_sidebar_statistics,
    default_sidebar_statistics,
)
from pairo.ui.models import EventDetailLevel, SystemTranscriptMetadata, TranscriptEntry
from pairo.ui.session_repository import StatisticValue
from pairo.ui import SlashCommand
from pairo.ui.navigation_events import AgentSwitchRequested, SessionSwitchRequested
from pairo.ui.sqlite_session_repository import SqliteSessionRepository
from pairo.ui.widgets.agent_sidebar import AgentSidebar
from pairo.ui.widgets.chat_editor import ChatEditor
from textual.widgets import Button, Collapsible, DataTable, Select, Static, TabbedContent


def make_agent(
    tmp_path: Path,
    pipeline: EventPipeline,
    agent_id: UUID | None = None,
    name: str = "reviewer",
) -> FakeAgent:
    return FakeAgent(
        AgentConfiguration(
            id=agent_id or uuid4(),
            name=name,
            sandbox=SandboxPermission.READ_ONLY,
            workspace=tmp_path,
            model="test",
        ),
        pipeline,
        lambda: "approved",
    )


def configured_app(
    agents, pipeline, repository, command_output_limit: int | None = None
) -> AgentChatApp:
    return AgentChatApp(
        agents, pipeline, repository,
        transcribers=(
            default_transcribers()
            if command_output_limit is None
            else default_transcribers(command_output_limit=command_output_limit)
        ),
        sidebar_statistics=create_default_sidebar_statistics(),
        commands=(
            HelpCommand(), AgentSelectionCommand(), SessionCommand(), SessionsCommand(),
            CompactCommand(), DeleteCommand(), InterruptCommand(), ImageCommand(),
        ),
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
        app = configured_app({agent_id: agent}, pipeline, repository)

        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.chats[agent_id].session_name == active.name
            stored = await repository.get_session(agent_id, active.name)
            assert stored.identifier == active.identifier.model_dump(mode="json")

        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_app_applies_ui_navigation_events_from_the_pipeline(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        first = make_agent(tmp_path, pipeline, name="first")
        second = make_agent(tmp_path, pipeline, name="second")
        first_id = first.configuration().id
        second_id = second.configuration().id
        first_session = await first.new_session("Conversation 1")
        await repository.save_session(
            first_id,
            first_session.name,
            first_session.identifier.model_dump(mode="json"),
        )
        active_first_session = await first.new_session("Current")
        await repository.save_session(
            first_id,
            active_first_session.name,
            active_first_session.identifier.model_dump(mode="json"),
        )
        second_session = await second.new_session("Review")
        await repository.save_session(
            second_id,
            second_session.name,
            second_session.identifier.model_dump(mode="json"),
        )
        app = configured_app(
            {first_id: first, second_id: second}, pipeline, repository
        )

        async with app.run_test() as pilot:
            pipeline.push(AgentSwitchRequested(agent_id=second_id))
            await pilot.pause()
            tabs = app.query_one("#agent-tabs", TabbedContent)
            assert tabs.active == "agent-chat-1"
            assert app.screen.focused is app.chats[second_id].input

            pipeline.push(
                SessionSwitchRequested(
                    agent_id=first_id,
                    session_name=first_session.name,
                )
            )
            # Event delivery includes asynchronous repository and widget work;
            # a single pilot pause does not guarantee that work has completed.
            async def wait_for_switch() -> None:
                while app.chats[first_id].session_name != first_session.name:
                    await pilot.pause()

            await asyncio.wait_for(wait_for_switch(), timeout=3)
            assert app.chats[first_id].session_name == first_session.name

            app.select_agent(first_id)
            editor = app.chats[first_id].input
            editor.text = f"/session switch {first_session.name}"
            await pilot.press("ctrl+enter")
            for _ in range(3):
                await pilot.pause()
            assert any(
                entry.content == f"Switched to session {first_session.name}."
                for entry in app.chats[first_id].transcript.entries
            )

            editor.text = f"/agent {second_id}"
            await pilot.press("ctrl+enter")
            for _ in range(3):
                await pilot.pause()
            assert any(
                entry.content == f"Selected agent second ({second_id})."
                for entry in app.chats[first_id].transcript.entries
            )
            assert tabs.active == "agent-chat-1"
            assert app.screen.focused is app.chats[second_id].input

            app.select_agent(first_id)
            editor = app.chats[first_id].input
            editor.text = f"/delete {first_session.name}"
            await pilot.press("ctrl+enter")
            async def wait_for_deletion() -> None:
                while not any(
                    entry.content == f"Deleted session {first_session.name}. Current session: {active_first_session.name}."
                    for entry in app.chats[first_id].transcript.entries
                ):
                    await pilot.pause()
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_for_deletion(), 3)
            assert app.chats[first_id].session_name == active_first_session.name
            assert [
                session.session_name
                for session in await repository.sessions_for(first_id)
            ] == [active_first_session.name]
            assert any(
                entry.content
                == f"Deleted session {first_session.name}. Current session: {active_first_session.name}."
                for entry in app.chats[first_id].transcript.entries
            )

            pipeline.push(SessionSwitchRequested(agent_id=first_id, session_name=first_session.name))
            pipeline.push(SessionSwitchRequested(agent_id=first_id, session_name="gone"))
            pipeline.push(AgentSwitchRequested(agent_id=uuid4()))
            await pilot.pause()
            assert app.chats[first_id].session_name == active_first_session.name
            assert app.chats[first_id].transcript.is_displaying_session(active_first_session.name)
            assert tabs.active == "agent-chat-0"

        await first.close()
        await second.close()
        await repository.close()

    asyncio.run(scenario())


def test_session_navigation_does_not_suppress_unrelated_repository_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        session = await agent.new_session("Current")
        agent_id = agent.configuration().id
        await repository.save_session(agent_id, session.name, session.identifier.model_dump(mode="json"))
        app = configured_app({agent_id: agent}, pipeline, repository)
        async with app.run_test() as pilot:
            await pilot.pause()

            async def fail_entries(*args: object) -> None:
                raise RuntimeError("repository failure")

            monkeypatch.setattr(repository, "entries_for", fail_entries)
            with pytest.raises(RuntimeError, match="repository failure"):
                await app.receive_session_switch_requested(
                    SessionSwitchRequested(agent_id=agent_id, session_name="Other")
                )
            assert app.chats[agent_id].transcript.is_displaying_session(session.name)
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
            configured_app({uuid4(): first, uuid4(): second}, pipeline, SqliteSessionRepository())

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
        app = configured_app({agent_id: restored_agent}, pipeline, repository)

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
        app = configured_app({agent_id: agent}, pipeline, repository)

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
        app = configured_app({agent_id: agent}, pipeline, repository)
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
        app = configured_app({agent_id: agent}, pipeline, repository)

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
        app = configured_app({agent_id: agent}, pipeline, repository)

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
        app = configured_app({agent_id: agent}, pipeline, repository)

        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._subscriptions

            await app.action_quit()

            assert not app._subscriptions

        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_app_closes_with_many_messages_queued_from_multiple_agents(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agents: dict[UUID, FakeAgent] = {}
        for index in range(5):
            agent = make_agent(tmp_path, pipeline, name=f"agent-{index}")
            agents[agent.configuration().id] = agent
        for index, agent in enumerate(agents.values()):
            active = await agent.new_session(f"Conversation {index}")
            await repository.save_session(
                agent.configuration().id,
                active.name,
                active.identifier.model_dump(mode="json"),
            )
        app = configured_app(agents, pipeline, repository)

        async with app.run_test() as pilot:
            await pilot.pause()
            messages = [
                (agent, f"message-{agent_index}-{message_index}")
                for agent_index, agent in enumerate(agents.values())
                for message_index in range(50)
            ]
            await asyncio.gather(
                *(agent.send_message(message) for agent, message in messages)
            )

            await asyncio.wait_for(app.action_quit(), timeout=2)
            assert not app._subscriptions

        await asyncio.gather(*(agent.close() for agent in agents.values()))
        await repository.close()

    asyncio.run(scenario())


def test_app_closes_with_thousands_of_messages_while_all_agents_are_idle(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agents: dict[UUID, FakeAgent] = {}
        session_names: dict[UUID, str] = {}
        for index in range(5):
            agent = make_agent(tmp_path, pipeline, name=f"idle-agent-{index}")
            active = await agent.new_session(f"Idle conversation {index}")
            agent_id = agent.configuration().id
            agents[agent_id] = agent
            session_names[agent_id] = active.name
            await repository.save_session(
                agent_id,
                active.name,
                active.identifier.model_dump(mode="json"),
            )
        app = configured_app(agents, pipeline, repository)

        async with app.run_test() as pilot:
            await pilot.pause()
            assert all(agent.status() is AgentStatus.IDLE for agent in agents.values())

            for agent_index, agent_id in enumerate(agents):
                for message_index in range(5_000):
                    pipeline.push(
                        MessageSent(
                            agent_id=agent_id,
                            session_name=session_names[agent_id],
                            message=f"loaded-message-{agent_index}-{message_index}",
                        )
                    )

            assert all(agent.status() is AgentStatus.IDLE for agent in agents.values())
            await asyncio.wait_for(app.action_quit(), timeout=2)
            assert not app._subscriptions

        await asyncio.gather(*(agent.close() for agent in agents.values()))
        await repository.close()

    asyncio.run(scenario())


def test_mounted_transcript_adds_completed_command_without_live_card(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        active = await agent.new_session("Conversation 1")
        agent_id = agent.configuration().id
        await repository.save_session(agent_id, active.name, active.identifier.model_dump(mode="json"))
        app = configured_app({agent_id: agent}, pipeline, repository, command_output_limit=64)
        async with app.run_test() as pilot:
            output = "H" * 40 + "discard" * 20 + "T" * 40
            pipeline.push(AgentCommandExecutionCompleted(
                agent_id=agent_id, session_name=active.name, activity_id="same",
                command="make", status="completed", output=output,
            ))
            for _ in range(3):
                await pilot.pause()
            transcript = app.chats[agent_id].transcript
            assert len(transcript.entries) == 1
            assert "Completed successfully" in transcript.entries[0].content
            assert "characters omitted" in transcript.entries[0].content
            persisted_entries = await repository.entries_for(agent_id, active.name)
            assert persisted_entries == tuple(transcript.entries)
            command_card = transcript.query_one(Collapsible)
            details = next(widget for widget in command_card.query(Static) if type(widget) is Static)
            assert "characters omitted" in str(details.render())
            pipeline.push(AgentMcpToolCallCompleted(
                agent_id=agent_id, session_name=active.name, activity_id="call",
                server="tools", tool="inspect", arguments={}, status="completed",
            ))
            for _ in range(3):
                await pilot.pause()
            assert len(transcript.entries) == 2
            assert transcript.entries[1].metadata["type"] == "mcp_tool_call"

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
        app = configured_app({agent_id: agent}, pipeline, repository)

        async with app.run_test() as pilot:
            external = await agent.new_session("External")
            event = SessionCreated(
                agent_id=agent_id,
                session_name=external.name,
                identifier=external.identifier.model_dump(mode="json"),
            )

            pipeline.push(event)
            for _ in range(3):
                await pilot.pause()

            stored = await repository.get_session(agent_id, external.name)
            assert stored.identifier == external.identifier.model_dump(mode="json")
            assert app.chats[agent_id].session_name == external.name

        await agent.close()
        await repository.close()

    asyncio.run(scenario())


class ExternalCommand(SlashCommand):
    name = "/external"

    async def invoke(self, event_pipeline, agent, agents, session_repository, argument) -> None:
        active = agent.current_session()
        assert active is not None
        entry = TranscriptEntry[SystemTranscriptMetadata](
            "system", f"external:{argument}", EventDetailLevel.NORMAL, {"type": "system"}
        )
        agent_id = agent.configuration().id
        from pairo.ui.transcript_events import AddTranscriptEntry
        event_pipeline.push(
            AddTranscriptEntry(agent_id=agent_id, session_name=active.name, entry=entry)
        )


def test_app_dispatches_only_explicitly_installed_external_commands(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        active = await agent.new_session("Conversation 1")
        agent_id = agent.configuration().id
        await repository.save_session(agent_id, active.name, active.identifier.model_dump(mode="json"))
        app = AgentChatApp(
            {agent_id: agent}, pipeline, repository,
            transcribers=default_transcribers(),
            sidebar_statistics=create_default_sidebar_statistics(),
            commands=(ExternalCommand(),),
        )
        async with app.run_test() as pilot:
            editor = app.chats[agent_id].input
            editor.text = "/external installed"
            await pilot.press("ctrl+enter")
            for _ in range(3):
                await pilot.pause()
            assert any(entry.content == "external:installed" for entry in app.chats[agent_id].transcript.entries)
            editor.text = "/help"
            await pilot.press("ctrl+enter")
            await pilot.pause()
            assert editor.text == "/help"
        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_token_usage_survives_fresh_application_reload(tmp_path: Path) -> None:
    from pairo.core.events import AgentTokenUsageUpdated, TokenUsage

    async def scenario() -> None:
        agent_id = uuid4()
        pipeline = EventPipeline()
        repository = SqliteSessionRepository(tmp_path)
        agent = make_agent(tmp_path, pipeline, agent_id)
        session = await agent.new_session("one")
        await repository.save_session(agent_id, session.name, session.identifier.model_dump(mode="json"))
        app = configured_app({agent_id: agent}, pipeline, repository)
        usage = TokenUsage(input_tokens=1, cached_input_tokens=0, output_tokens=2, reasoning_output_tokens=0, total_tokens=3)
        async with app.run_test() as pilot:
            pipeline.push(AgentTokenUsageUpdated(
                agent_id=agent_id, session_name=session.name, last=usage, total=usage,
            ))
            for _ in range(10):
                stored_values = await repository.session_statistics_for(
                    agent_id, session.name
                )
                if any(
                    value.key == "token_usage" and value.value == "3 tokens"
                    for value in stored_values
                ):
                    break
                await pilot.pause()
            assert any(
                value.key == "token_usage" and value.value == "3 tokens"
                for value in await repository.session_statistics_for(agent_id, session.name)
            )
            for _ in range(3):
                await pilot.pause()
            assert app.chats[agent_id].sidebar.session_values["token_usage"] == "3 tokens"
        await agent.close(); await repository.close()

        reloaded_repository = SqliteSessionRepository(tmp_path)
        reloaded_pipeline = EventPipeline()
        reloaded_agent = make_agent(tmp_path, reloaded_pipeline, agent_id)
        reloaded_app = configured_app(
            {agent_id: reloaded_agent}, reloaded_pipeline, reloaded_repository,
        )
        async with reloaded_app.run_test() as pilot:
            sidebar = reloaded_app.chats[agent_id].sidebar
            sidebar.query_one(".sidebar-information-picker", Select).value = "session"
            await pilot.pause()
            assert ("token_usage", "Token usage", "3 tokens") in sidebar.query_one(
                ".sidebar-information"
            )._fields
        await reloaded_agent.close(); await reloaded_repository.close()

    asyncio.run(scenario())

def test_sidebar_renders_selected_session_values_after_replacement_and_deletion(
    tmp_path: Path,
) -> None:
    """Session transitions refresh sidebar fields and reject stale updates."""

    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        first = await agent.new_session("first")
        second = await agent.new_session("second")
        agent_id = agent.configuration().id
        for session in (first, second):
            await repository.save_session(
                agent_id, session.name, session.identifier.model_dump(mode="json")
            )
        await repository.upsert_session_statistic(
            agent_id, first.name, StatisticValue("token_usage", "first tokens")
        )
        await repository.upsert_session_statistic(
            agent_id, second.name, StatisticValue("token_usage", "second tokens")
        )
        app = configured_app({agent_id: agent}, pipeline, repository)
        async with app.run_test() as pilot:
            sidebar = app.chats[agent_id].sidebar

            async def displayed_values() -> tuple[tuple[str, str, str], ...]:
                sidebar.query_one(".sidebar-information-picker", Select).value = "session"
                await pilot.pause()
                return sidebar.query_one(".sidebar-information")._fields

            assert ("token_usage", "Token usage", "second tokens") in await displayed_values()
            await agent.activate_session(first.name, first.identifier)
            await app.chats[agent_id].select_session(first.name)
            assert ("token_usage", "Token usage", "first tokens") in await displayed_values()

            app.post_message(UpdateSessionStatistic("token_usage", "stale", agent_id, second.name))
            await pilot.pause()
            assert ("token_usage", "Token usage", "first tokens") in await displayed_values()

            editor = app.chats[agent_id].input
            editor.text = "/session new replacement"
            await pilot.press("ctrl+enter")
            for _ in range(3):
                await pilot.pause()
            assert app.chats[agent_id].session_name == "replacement"
            assert ("token_usage", "Token usage", "Unavailable") in await displayed_values()

            editor.text = "/delete replacement"
            await pilot.press("ctrl+enter")
            for _ in range(3):
                await pilot.pause()
            assert app.chats[agent_id].session_name == second.name
            assert ("token_usage", "Token usage", "second tokens") in await displayed_values()
        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_agent_state_statistic_updates_visible_sidebar_from_lifecycle_events(
    tmp_path: Path,
) -> None:
    """The agent-state field changes without sidebar polling or navigation."""
    from pairo.core.events import AgentResponseCompleted, MessageSent
    from pairo.ui.widgets.sidebar_statistics.default_sidebar_statistics import (
        AgentStateStatistic, DefaultSidebarStatisticValues,
    )

    class TrackingState(AgentStateStatistic):
        def __init__(self) -> None:
            super().__init__(DefaultSidebarStatisticValues())
            self.delivered: asyncio.Queue[str | None] = asyncio.Queue()

        async def update_agent(self, agent_id: UUID, value: str | None) -> None:
            await super().update_agent(agent_id, value)
            self.delivered.put_nowait(value)

    class ReadyApp(AgentChatApp):
        async def on_mount(self) -> None:
            await super().on_mount()
            self.ready.set()

    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        session = await agent.new_session("state")
        agent_id = agent.configuration().id
        await repository.save_session(
            agent_id, session.name, session.identifier.model_dump(mode="json")
        )
        state = TrackingState()
        statistics = tuple(
            state if item.key == "state" else item
            for item in create_default_sidebar_statistics()
        )
        app = ReadyApp(
            {agent_id: agent}, pipeline, repository,
            transcribers=default_transcribers(), sidebar_statistics=statistics, commands=(),
        )
        app.ready = asyncio.Event()
        async with app.run_test() as pilot:
            await asyncio.wait_for(app.ready.wait(), 3)
            sidebar = app.chats[agent_id].sidebar

            from pairo.ui.widgets.sidebar_field import SidebarField
            from pairo.ui.widgets.sidebar_statistics import SidebarStatisticCategory

            def visible_state() -> str:
                for field in sidebar.query(SidebarField):
                    if field.key == "state":
                        return str(field.query_one(".sidebar-field-value", Static).render())
                return ""

            async def wait_for_field(value: str) -> None:
                while value not in visible_state():
                    await asyncio.sleep(0.01)

            async def wait_for_value(value: str) -> None:
                while await state.delivered.get() != value:
                    pass

            await asyncio.wait_for(wait_for_field("idle"), 3)
            captured = asyncio.Event()
            release = asyncio.Event()
            original_values_for = sidebar._values_for
            held = False

            async def delayed_values(category, session_name):
                nonlocal held
                values = await original_values_for(category, session_name)
                if category is SidebarStatisticCategory.AGENT and not held:
                    held = True
                    captured.set()
                    await release.wait()
                return values

            sidebar._values_for = delayed_values
            pending_load = asyncio.create_task(sidebar.load(session.name))
            try:
                await asyncio.wait_for(captured.wait(), 3)
                pipeline.push(MessageSent(
                    agent_id=agent_id, session_name=session.name,
                    message="start", source="user",
                ))
                await asyncio.wait_for(wait_for_value("generating"), 3)
                await asyncio.wait_for(wait_for_field("generating"), 3)
            finally:
                release.set()
                await asyncio.wait_for(pending_load, 3)
                sidebar._values_for = original_values_for

            await asyncio.wait_for(wait_for_field("generating"), 3)
            assert "generating" in visible_state()
            pipeline.push(AgentResponseCompleted(
                agent_id=agent_id, session_name=session.name, message="done"
            ))
            await asyncio.wait_for(wait_for_value("idle"), 3)
            await asyncio.wait_for(wait_for_field("idle"), 3)
            assert "idle" in visible_state()
        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_state_events_route_through_statistic_and_isolate_agents(tmp_path: Path) -> None:
    from pairo.core.events import AgentError, AgentResponseCompleted, AgentResponseInterrupted, SessionUpdated
    from pairo.ui.widgets.sidebar_statistics.default_sidebar_statistics import AgentStateStatistic, DefaultSidebarStatisticValues

    class CountingState(AgentStateStatistic):
        def __init__(self) -> None:
            super().__init__(DefaultSidebarStatisticValues())
            self.updates: list[tuple[UUID, str | None]] = []
            self.completed = asyncio.Event()

        async def update_agent(self, agent_id: UUID, value: str | None) -> None:
            self.updates.append((agent_id, value))
            await super().update_agent(agent_id, value)
            self.completed.set()

    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        first = make_agent(tmp_path, pipeline, name="first")
        second = make_agent(tmp_path, pipeline, name="second")
        first_id, second_id = first.configuration().id, second.configuration().id
        await first.new_session("s")
        await second.new_session("s")
        state = CountingState()
        app = AgentChatApp({first_id: first, second_id: second}, pipeline, repository,
                           transcribers=default_transcribers(), sidebar_statistics=(state,), commands=())
        async with app.run_test() as pilot:
            sidebar = app.chats[first_id].sidebar
            other = app.chats[second_id].sidebar
            events = [
                (MessageSent(agent_id=first_id, session_name="s", message="hi", source="user"), "generating"),
                (MessageSent(agent_id=first_id, session_name="s", message="hi", source="first"), "idle"),
                (MessageSent(agent_id=first_id, session_name="s", message="hi", source="second"), "generating"),
                (AgentResponseCompleted(agent_id=first_id, session_name="s", message="done"), "idle"),
                (MessageSent(agent_id=first_id, session_name="s", message="hi"), "generating"),
                (AgentResponseInterrupted(agent_id=first_id, session_name="s"), "idle"),
                (MessageSent(agent_id=first_id, session_name="s", message="hi"), "generating"),
                (AgentError(agent_id=first_id, session_name="s", message="error"), "idle"),
                (MessageSent(agent_id=first_id, session_name="s", message="hi"), "generating"),
                (SessionCreated(agent_id=first_id, session_name="s", identifier={}), "idle"),
                (MessageSent(agent_id=first_id, session_name="s", message="hi"), "generating"),
                (SessionUpdated(agent_id=first_id, session_name="s", identifier={}), "idle"),
            ]
            for index, (event, expected) in enumerate(events, 1):
                state.completed.clear()
                pipeline.push(event)
                await asyncio.wait_for(state.completed.wait(), 3)
                for _ in range(3):
                    await pilot.pause()
                assert sidebar.agent_values["state"] == expected, (index, event, state.updates)
                assert ("state", "State", expected) in sidebar.query_one(".sidebar-information")._fields
                assert other.agent_values["state"] == "idle"
                assert len(state.updates) == index
            pipeline.push(MessageSent(agent_id=uuid4(), session_name="s", message="unknown"))
            await pilot.pause()
            assert len(state.updates) == len(events)
            pipeline.push(AgentSwitchRequested(agent_id=second_id))
            for _ in range(3):
                await pilot.pause()
            for event, expected in (
                (MessageSent(agent_id=second_id, session_name="s", message="hi"), "generating"),
                (AgentResponseCompleted(agent_id=second_id, session_name="s", message="done"), "idle"),
            ):
                state.completed.clear()
                pipeline.push(event)
                await asyncio.wait_for(state.completed.wait(), 3)
                for _ in range(3):
                    await pilot.pause()
                assert ("state", "State", expected) in other.query_one(".sidebar-information")._fields
                assert ("state", "State", "idle") in sidebar.query_one(".sidebar-information")._fields
            assert len(state.updates) == len(events) + 2
        await first.close(); await second.close(); await repository.close()
    asyncio.run(scenario())


def test_state_events_without_state_statistic_do_not_publish_updates(tmp_path: Path) -> None:
    from pairo.core.events import AgentError, AgentResponseCompleted, AgentResponseInterrupted, SessionUpdated
    from pairo.ui.widgets.sidebar_statistics import UpdateAgentStatistic
    from textual import on

    class ObservingApp(AgentChatApp):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.updates = []

        @on(UpdateAgentStatistic)
        def observe_update(self, message: UpdateAgentStatistic) -> None:
            self.updates.append(message.key)

    async def scenario() -> None:
        pipeline = EventPipeline(); repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline); agent_id = agent.configuration().id
        app = ObservingApp({agent_id: agent}, pipeline, repository,
                           transcribers=default_transcribers(), sidebar_statistics=(), commands=())
        async with app.run_test() as pilot:
            for event in (MessageSent(agent_id=agent_id, session_name="s", message="hi"),
                          AgentResponseCompleted(agent_id=agent_id, session_name="s", message="done"),
                          AgentResponseInterrupted(agent_id=agent_id, session_name="s"),
                          AgentError(agent_id=agent_id, session_name="s", message="error"),
                          SessionCreated(agent_id=agent_id, session_name="s", identifier={}),
                          SessionUpdated(agent_id=agent_id, session_name="s", identifier={})):
                pipeline.push(event)
            for _ in range(3): await pilot.pause()
            assert "state" not in app.chats[agent_id].sidebar.agent_values
            assert app.updates == []
        await agent.close(); await repository.close()
    asyncio.run(scenario())


def test_app_transcript_requests_commit_once_and_show_mutations(tmp_path: Path) -> None:
    from pairo.ui.transcript_events import AddTranscriptEntry, UpdateTranscriptEntry, DeleteTranscriptEntry

    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        session = await agent.new_session("requests")
        agent_id = agent.configuration().id
        app = configured_app({agent_id: agent}, pipeline, repository)
        writes = []
        for name in ("add_entry", "update_entry", "delete_entry"):
            original_method = getattr(repository, name)
            async def counted(*args, _name=name, _method=original_method):
                writes.append(_name)
                return await _method(*args)
            setattr(repository, name, counted)
        original = TranscriptEntry[SystemTranscriptMetadata]("system", "one", EventDetailLevel.NORMAL, {"type": "system"})
        updated = TranscriptEntry[SystemTranscriptMetadata]("system", "two", EventDetailLevel.NORMAL, {"type": "system"}, original.entry_id)
        async with app.run_test() as pilot:
            # Queue all operations without waiting, preserving request order.
            pipeline.push(AddTranscriptEntry(agent_id=agent_id, session_name=session.name, entry=original))
            pipeline.push(UpdateTranscriptEntry(agent_id=agent_id, session_name=session.name, entry=updated))
            async def wait_for_updated() -> None:
                while app.chats[agent_id].transcript.entries != [updated]:
                    await pilot.pause()
            await asyncio.wait_for(wait_for_updated(), 3)
            assert await repository.entries_for(agent_id, session.name) == (updated,)
            pipeline.push(DeleteTranscriptEntry(agent_id=agent_id, session_name=session.name, entry_id=original.entry_id))
            async def wait_for_deleted() -> None:
                while app.chats[agent_id].transcript.entries:
                    await pilot.pause()
            await asyncio.wait_for(wait_for_deleted(), 3)
            assert await repository.entries_for(agent_id, session.name) == ()
            assert writes == ["add_entry", "update_entry", "delete_entry"]
            pipeline.push(AddTranscriptEntry(agent_id=agent_id, session_name="missing", entry=original))
            pipeline.push(UpdateTranscriptEntry(agent_id=agent_id, session_name="missing", entry=updated))
            await pilot.pause()
            assert app.chats[agent_id].transcript.entries == []
        await agent.close()
        await repository.close()
    asyncio.run(scenario())


def test_permission_card_decision_uses_app_boundary(tmp_path: Path) -> None:
    from pairo.core.events import PermissionRequest, PermissionResponse
    from pairo.core.permissions import CommandExecutionApprovalRequest
    from pairo.ui.transcribers import PermissionRequestCard
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        session = await agent.new_session("permission")
        agent_id = agent.configuration().id
        app = configured_app({agent_id: agent}, pipeline, repository)
        responses = []
        subscription = pipeline.subscribe(PermissionResponse, lambda event, _: responses.append(event))
        async with app.run_test() as pilot:
            pipeline.push(PermissionRequest(request_id="p1", agent_id=agent_id, session_name=session.name, request=CommandExecutionApprovalRequest(session_identifier={}, command="echo hi")))
            for _ in range(4):
                await pilot.pause()
            card = app.query_one(PermissionRequestCard)
            card.query(Button)[0].press()
            for _ in range(4):
                await pilot.pause()
            assert len(responses) == 1
            stored = await repository.entries_for(agent_id, session.name)
            assert stored[0].metadata["status"] == "resolved"
            assert stored[0].metadata["decision"] == "accept"
            assert app.chats[agent_id].transcript.entries[0] == stored[0]
        pipeline.unsubscribe(subscription)
        await agent.close()
        await repository.close()
    asyncio.run(scenario())



def test_permission_card_listens_for_matching_external_response(tmp_path: Path) -> None:
    from pairo.core.events import PermissionRequest, PermissionResponse
    from pairo.core.permissions import CommandExecutionApprovalRequest, PermissionDecision
    from pairo.ui.transcribers import PermissionRequestCard

    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        session = await agent.new_session("external-permission")
        agent_id = agent.configuration().id
        app = configured_app({agent_id: agent}, pipeline, repository)
        async with app.run_test() as pilot:
            pipeline.push(PermissionRequest(request_id="external", agent_id=agent_id, session_name=session.name, request=CommandExecutionApprovalRequest(session_identifier={}, command="echo hi")))
            for _ in range(4):
                await pilot.pause()
            card = app.query_one(PermissionRequestCard)
            pipeline.push(PermissionResponse(request_id="other", agent_id=agent_id, session_name=session.name, decision=PermissionDecision.DECLINE))
            await pilot.pause()
            assert not card.query(Button)[0].disabled
            pipeline.push(PermissionResponse(request_id="external", agent_id=agent_id, session_name=session.name, decision=PermissionDecision.DECLINE))
            for _ in range(4):
                await pilot.pause()
            stored = await repository.entries_for(agent_id, session.name)
            assert stored[0].metadata["decision"] == "decline"
            assert all(button.disabled for button in app.query_one(PermissionRequestCard).query(Button))
        await agent.close()
        await repository.close()
    asyncio.run(scenario())


def test_permission_persistence_failure_keeps_decision_visible(tmp_path: Path) -> None:
    from pairo.core.events import PermissionRequest, PermissionResponse
    from pairo.core.permissions import CommandExecutionApprovalRequest
    from pairo.ui.transcribers import PermissionRequestCard
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        session = await agent.new_session("permission-failure")
        agent_id = agent.configuration().id
        app = configured_app({agent_id: agent}, pipeline, repository)
        responses = []
        subscription = pipeline.subscribe(PermissionResponse, lambda event, _: responses.append(event))
        async with app.run_test() as pilot:
            pipeline.push(PermissionRequest(request_id="p2", agent_id=agent_id, session_name=session.name, request=CommandExecutionApprovalRequest(session_identifier={}, command="echo hi")))
            for _ in range(4):
                await pilot.pause()
            async def fail_update(*args):
                raise RuntimeError("write failed")
            repository.update_entry = fail_update
            card = app.query_one(PermissionRequestCard)
            card.query(Button)[0].press()
            for _ in range(4):
                await pilot.pause()
            assert len(responses) == 1
            assert "Decision:" in str(card.query_one(".permission-decision", Static).render())
            stored = await repository.entries_for(agent_id, session.name)
            assert stored[0].metadata["status"] == "pending"
            assert len(stored) == 1
        pipeline.unsubscribe(subscription)
        await agent.close()
        await repository.close()
    asyncio.run(scenario())


def test_statistic_routes_update_sidebar_synchronously(tmp_path: Path) -> None:
    from pairo.ui.widgets.sidebar_statistics import UpdateAgentStatistic, UpdateSessionStatistic

    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        session = await agent.new_session("direct-sidebar")
        agent_id = agent.configuration().id
        app = configured_app({agent_id: agent}, pipeline, repository)
        async with app.run_test() as pilot:
            await pilot.pause()
            sidebar = app.chats[agent_id].sidebar
            app.route_agent_statistic(UpdateAgentStatistic("state", "direct", agent_id))
            assert sidebar.agent_values["state"] == "direct"
            app.route_session_statistic(UpdateSessionStatistic("token_usage", "123", agent_id, session.name))
            assert sidebar.session_values["token_usage"] == "123"
            app.route_session_statistic(UpdateSessionStatistic("token_usage", "wrong", agent_id, "other"))
            assert sidebar.session_values["token_usage"] == "123"
            app.route_agent_statistic(UpdateAgentStatistic("state", "wrong", uuid4()))
            assert sidebar.agent_values["state"] == "direct"
        await agent.close()
        await repository.close()
    asyncio.run(scenario())


def test_transcript_update_request_calls_pane_before_returning(tmp_path: Path) -> None:
    from pairo.ui.transcript_events import UpdateTranscriptEntry
    from pairo.ui.transcribers import RoutedTranscriptEntry

    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        session = await agent.new_session("direct-update")
        agent_id = agent.configuration().id
        app = configured_app({agent_id: agent}, pipeline, repository)
        entry = TranscriptEntry[SystemTranscriptMetadata]("system", "before", EventDetailLevel.NORMAL, {"type": "system"})
        updated = TranscriptEntry[SystemTranscriptMetadata]("system", "after", EventDetailLevel.NORMAL, {"type": "system"}, entry.entry_id)
        async with app.run_test() as pilot:
            await app._add_routed_entry(RoutedTranscriptEntry(agent_id, session.name, entry))
            await pilot.pause()
            await app._update_transcript_request(UpdateTranscriptEntry(agent_id=agent_id, session_name=session.name, entry=updated))
            assert app.chats[agent_id].transcript.entries == [updated]
            assert await repository.entries_for(agent_id, session.name) == (updated,)
        await agent.close()
        await repository.close()
    asyncio.run(scenario())
