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
from pairo.ui.widgets.sidebar_statistics import default_sidebar_statistics
from pairo.ui.models import EventDetailLevel, SystemTranscriptMetadata, TranscriptEntry
from pairo.ui.session_repository import StatisticValue
from pairo.ui import SlashCommand
from pairo.ui.navigation_events import AgentSwitchRequested, SessionSwitchRequested
from pairo.ui.sqlite_session_repository import SqliteSessionRepository
from pairo.ui.transcript_events import TranscriptEntryAdded
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
        sidebar_statistics={agent_id: default_sidebar_statistics(agent) for agent_id, agent in agents.items()},
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
            await pilot.pause()
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
            for _ in range(3):
                await pilot.pause()
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

            pipeline.push(SessionSwitchRequested(agent_id=first_id, session_name="gone"))
            pipeline.push(AgentSwitchRequested(agent_id=uuid4()))
            await pilot.pause()
            assert app.chats[first_id].session_name == active_first_session.name
            assert tabs.active == "agent-chat-0"

        await first.close()
        await second.close()
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
        await session_repository.add_entry(agent_id, active.name, entry)
        event_pipeline.push(
            TranscriptEntryAdded(agent_id=agent_id, session_name=active.name, entry=entry)
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
            sidebar_statistics={agent_id: default_sidebar_statistics(agent)},
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


def test_sidebar_rejects_value_equal_recreated_session_statistic(tmp_path: Path) -> None:
    """A delayed save may not cross an activation boundary by value equality."""
    from pairo.ui.widgets.sidebar_statistics import SaveSessionStatistic
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent(tmp_path, pipeline)
        first = await agent.new_session("one")
        app = configured_app({agent.configuration().id: agent}, pipeline, repository)
        async with app.run_test() as pilot:
            sidebar = app.chats[agent.configuration().id].sidebar
            recreated = first.model_copy()
            agent._active_session = recreated  # test the provider recreating an equal identity
            app.post_message(SaveSessionStatistic("late", "wrong", agent, first))
            await pilot.pause()
            assert "late" not in sidebar.session_values
            await pilot.pause()
        await agent.close()
        await repository.close()
    asyncio.run(scenario())


def test_sidebar_statistics_redesign_has_no_pipeline_polling_or_provider_protocol() -> None:
    from pairo.ui.widgets.sidebar_statistics import SidebarStatistic, TokenUsageStatistic
    from pairo.ui.widgets.sidebar_statistics import statistic as statistic_module
    from pairo.ui.widgets.sidebar_statistics import token_usage_statistic as token_module
    from pairo.ui import app as app_module
    assert issubclass(TokenUsageStatistic, SidebarStatistic)
    assert "EventPipeline" not in statistic_module.__dict__
    assert "EventPipeline" not in token_module.__dict__
    assert "set_interval(0.25" not in Path(app_module.__file__).read_text()
    assert "_refresh_sidebars" not in Path(app_module.__file__).read_text()


def test_custom_message_pump_statistic_activation_and_categories(tmp_path: Path) -> None:
    """Documented custom migration: subclass base, compute, and category ownership."""
    from pairo.ui.widgets.sidebar_statistics import (
        FixedStatistic, SidebarStatistic, SidebarStatisticCategory,
        SidebarStatisticContext, SidebarStatistics,
    )
    class CountingStatistic(SidebarStatistic):
        def __init__(self) -> None:
            super().__init__("custom", "Custom", SidebarStatisticCategory.AGENT)
            self.computes = 0
        async def compute(self, context: SidebarStatisticContext) -> object:
            self.computes += 1
            return f"{context.agent.configuration().name}:{self.computes}"
    async def scenario() -> None:
        pipeline = EventPipeline(); repository = SqliteSessionRepository(); agent = make_agent(tmp_path, pipeline)
        custom = CountingStatistic()
        statistics = SidebarStatistics((custom,), (FixedStatistic("session", "Session", lambda _context: _constant("S"), SidebarStatisticCategory.SESSION),))
        app = AgentChatApp({agent.configuration().id: agent}, pipeline, repository, transcribers=default_transcribers(), sidebar_statistics={agent.configuration().id: statistics}, commands=())
        async with app.run_test() as pilot:
            sidebar = app.chats[agent.configuration().id].sidebar
            assert custom.computes == 1
            await app._rebuild_statistics(agent.configuration().id)
            assert custom.computes == 2  # rebuild does not configure a second pump
            sidebar._information_mode = sidebar._AGENT_INFORMATION
            await sidebar._refresh_information_fields()
            assert sidebar.query_one(".sidebar-information")._fields == (("custom", "Custom", "reviewer:2"),)
            sidebar._information_mode = sidebar._SESSION_INFORMATION
            await sidebar._refresh_information_fields()
            assert sidebar.query_one(".sidebar-information")._fields == ()
            await pilot.pause()
        await agent.close(); await repository.close()
    asyncio.run(scenario())


async def _constant(value: object) -> object:
    return value


def test_sidebar_statistics_reject_duplicate_configured_keys() -> None:
    from pairo.ui.widgets.sidebar_statistics import FixedStatistic, SidebarStatistics
    with pytest.raises(ValueError, match="Duplicate sidebar statistic keys"):
        SidebarStatistics((FixedStatistic("same", "One", _constant), FixedStatistic("same", "Two", _constant)), ())


def test_token_usage_uses_only_relevant_bridged_event(tmp_path: Path) -> None:
    from pairo.core.events import AgentTokenUsageUpdated, TokenUsage
    async def scenario() -> None:
        pipeline = EventPipeline(); repository = SqliteSessionRepository(); agent = make_agent(tmp_path, pipeline)
        session = await agent.new_session("one"); usage = TokenUsage(input_tokens=1, cached_input_tokens=0, output_tokens=2, reasoning_output_tokens=0, total_tokens=3)
        app = configured_app({agent.configuration().id: agent}, pipeline, repository)
        async with app.run_test() as pilot:
            sidebar = app.chats[agent.configuration().id].sidebar
            pipeline.push(AgentTokenUsageUpdated(agent_id=agent.configuration().id, session_name="one", last=usage, total=usage))
            await pilot.pause(); await pilot.pause()
            assert dict(
                (item.key, item.value)
                for item in await repository.session_statistics_for(
                    agent.configuration().id, session.name
                )
            )["token_usage"] == "3 tokens"
            sidebar.query_one(".sidebar-information-picker", Select).value = "session"
            await pilot.pause()
            assert ("token_usage", "Token usage", "3 tokens") in sidebar.query_one(
                ".sidebar-information"
            )._fields
            pipeline.push(AgentTokenUsageUpdated(agent_id=uuid4(), session_name="one", last=usage, total=usage))
            pipeline.push(AgentTokenUsageUpdated(agent_id=agent.configuration().id, session_name="other", last=usage, total=usage))
            await pilot.pause()
            assert ("token_usage", "Token usage", "3 tokens") in sidebar.query_one(
                ".sidebar-information"
            )._fields
        await agent.close(); await repository.close()
    asyncio.run(scenario())


def test_sidebar_statistics_keep_session_values_isolated_when_switching(tmp_path: Path) -> None:
    """Mounted sidebars restore the selected session's event-driven value."""
    from pairo.core.events import AgentTokenUsageUpdated, TokenUsage

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
        app = configured_app({agent_id: agent}, pipeline, repository)
        usage = TokenUsage(
            input_tokens=1, cached_input_tokens=0, output_tokens=2,
            reasoning_output_tokens=0, total_tokens=3,
        )
        async with app.run_test() as pilot:
            sidebar = app.chats[agent_id].sidebar
            pipeline.push(AgentTokenUsageUpdated(
                agent_id=agent_id, session_name="second", last=usage, total=usage
            ))
            await pilot.pause()
            await pilot.pause()
            assert sidebar.session_values["token_usage"] == "3 tokens"
            await agent.activate_session("first", first.identifier)
            await app.chats[agent_id].select_session("first")
            await app._rebuild_statistics(agent_id)
            await pilot.pause()
            assert "token_usage" not in sidebar.session_values
            await agent.activate_session("second", second.identifier)
            await app.chats[agent_id].select_session("second")
            await app._rebuild_statistics(agent_id)
            await pilot.pause()
            assert sidebar.session_values["token_usage"] == "3 tokens"
        await agent.close()
        await repository.close()

    asyncio.run(scenario())


def test_shared_statistics_configuration_scopes_builds_and_events(tmp_path: Path) -> None:
    from pairo.core.events import AgentTokenUsageUpdated, TokenUsage
    from pairo.ui.widgets.sidebar_statistics import (
        SidebarStatistic,
        SidebarStatisticCategory,
        SidebarStatisticContext,
        SidebarStatistics,
        TokenUsageStatistic,
    )

    class AgentNameStatistic(SidebarStatistic):
        def __init__(self) -> None:
            super().__init__("agent", "Agent", SidebarStatisticCategory.AGENT)
        async def compute(self, context: SidebarStatisticContext) -> str:
            return context.agent.configuration().name

    async def scenario() -> None:
        pipeline = EventPipeline(); repository = SqliteSessionRepository()
        first = make_agent(tmp_path, pipeline, name="first"); second = make_agent(tmp_path, pipeline, name="second")
        first_session = await first.new_session("one")
        second_session = await second.new_session("one")
        for agent, session in ((first, first_session), (second, second_session)):
            await repository.save_session(
                agent.configuration().id,
                session.name,
                session.identifier.model_dump(mode="json"),
            )
        shared = SidebarStatistics((AgentNameStatistic(),), (TokenUsageStatistic(),))
        app = AgentChatApp(
            {first.configuration().id: first, second.configuration().id: second},
            pipeline,
            repository,
            transcribers=default_transcribers(),
            sidebar_statistics={
                first.configuration().id: shared,
                second.configuration().id: shared,
            },
            commands=(),
        )
        usage = TokenUsage(input_tokens=1, cached_input_tokens=0, output_tokens=2, reasoning_output_tokens=0, total_tokens=3)
        async with app.run_test() as pilot:
            assert app.chats[first.configuration().id].sidebar.agent_values["agent"] == "first"
            assert app.chats[second.configuration().id].sidebar.agent_values["agent"] == "second"
            pipeline.push(AgentTokenUsageUpdated(
                agent_id=first.configuration().id, session_name="one", last=usage, total=usage,
            ))
            await pilot.pause(); await pilot.pause()
            assert app.chats[first.configuration().id].sidebar.session_values["token_usage"] == "3 tokens"
            assert "token_usage" not in app.chats[second.configuration().id].sidebar.session_values
        await first.close(); await second.close(); await repository.close()
    asyncio.run(scenario())


def test_shared_statistics_rebuild_scopes_after_navigation_and_replacement(tmp_path: Path) -> None:
    """Shared modules retain each agent/session's committed value through UI navigation."""
    from pairo.ui.widgets.sidebar_statistics import (
        SidebarStatistic,
        SidebarStatisticCategory,
        SidebarStatisticContext,
        SidebarStatistics,
        TokenUsageStatistic,
    )

    class AgentNameStatistic(SidebarStatistic):
        def __init__(self) -> None:
            super().__init__("agent_name", "Agent", SidebarStatisticCategory.AGENT)

        async def compute(self, context: SidebarStatisticContext) -> str:
            return context.agent.configuration().name

    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        first = make_agent(tmp_path, pipeline, name="first")
        second = make_agent(tmp_path, pipeline, name="second")
        first_id, second_id = first.configuration().id, second.configuration().id
        first_one = await first.new_session("one")
        first_two = await first.new_session("two")
        second_one = await second.new_session("one")
        for agent, session in (
            (first, first_one), (first, first_two), (second, second_one),
        ):
            await repository.save_session(
                agent.configuration().id,
                session.name,
                session.identifier.model_dump(mode="json"),
            )
        await repository.upsert_session_statistic(
            first_id, "one", StatisticValue("token_usage", "first-one")
        )
        await repository.upsert_session_statistic(
            first_id, "two", StatisticValue("token_usage", "first-two")
        )
        await repository.upsert_session_statistic(
            second_id, "one", StatisticValue("token_usage", "second-one")
        )
        shared = SidebarStatistics((AgentNameStatistic(),), (TokenUsageStatistic(),))
        app = AgentChatApp(
            {first_id: first, second_id: second}, pipeline, repository,
            transcribers=default_transcribers(),
            sidebar_statistics={first_id: shared, second_id: shared}, commands=(
                SessionCommand(), DeleteCommand(),
            ),
        )
        async with app.run_test() as pilot:
            async def visible_fields(
                agent_id: UUID, mode: str
            ) -> tuple[tuple[str, str, str], ...]:
                sidebar = app.chats[agent_id].sidebar
                sidebar.query_one(".sidebar-information-picker", Select).value = mode
                await pilot.pause()
                return sidebar.query_one(".sidebar-information")._fields

            assert ("agent_name", "Agent", "first") in await visible_fields(first_id, "agent")
            assert ("token_usage", "Token usage", "first-two") in await visible_fields(first_id, "session")
            assert ("agent_name", "Agent", "second") in await visible_fields(second_id, "agent")
            assert ("token_usage", "Token usage", "second-one") in await visible_fields(second_id, "session")

            first_editor = app.chats[first_id].input
            first_editor.text = "/session switch one"
            await pilot.press("ctrl+enter")
            for _ in range(3):
                await pilot.pause()
            assert ("token_usage", "Token usage", "first-one") in await visible_fields(first_id, "session")

            first_editor.text = "/session new replacement"
            await pilot.press("ctrl+enter")
            for _ in range(3):
                await pilot.pause()
            assert all(key != "token_usage" for key, _, _ in await visible_fields(first_id, "session"))

            first_editor.text = "/delete replacement"
            await pilot.press("ctrl+enter")
            for _ in range(3):
                await pilot.pause()
            assert app.chats[first_id].session_name == "two"
            assert ("token_usage", "Token usage", "first-two") in await visible_fields(first_id, "session")
            assert ("token_usage", "Token usage", "second-one") in await visible_fields(second_id, "session")

            app.select_agent(second_id)
            await pilot.pause()
            assert ("agent_name", "Agent", "second") in await visible_fields(second_id, "agent")
            assert ("token_usage", "Token usage", "second-one") in await visible_fields(second_id, "session")
        await first.close()
        await second.close()
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
            await pilot.pause(); await pilot.pause()
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
