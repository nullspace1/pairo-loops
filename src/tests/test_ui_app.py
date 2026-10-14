"""Headless smoke test for the async Textual integration."""

import asyncio
from dataclasses import replace

import pytest
from textual.widgets import Button

from pairo.core.agent import Agent, AgentState, SandboxMode
from pairo.core.events import (
    AgentError,
    AgentTokenUsageUpdated,
    EventPipeline,
    MessageSent,
    PermissionRequest,
    PermissionResponse,
    SessionCreated,
    SessionUpdated,
    TokenUsage,
)
from pairo.core.permissions import PermissionRequestType
from pairo.ui.app import AgentChatApp
from pairo.ui.transcribers import (
    AgentErrorTranscriber,
    MessageSentTranscriber,
    PermissionRequestCard,
    RoutedTranscriptEntry,
    Transcriber,
)
from pairo.ui.models import EventDetailLevel, TranscriptEntry
from pairo.ui.sqlite_session_repository import SqliteSessionRepository
from pairo.ui.transcript_events import (
    TranscriptEntryAdded,
    TranscriptEntryDeleted,
    TranscriptEntryUpdated,
)


class CountingSessionRepository(SqliteSessionRepository):
    def __init__(self) -> None:
        super().__init__()
        self.save_count = 0

    async def save_session(self, agent_id, session_name, identifier):
        self.save_count += 1
        return await super().save_session(agent_id, session_name, identifier)


class FakeRunner:
    """Runner fake owns sessions and emits the same events as the shared runner."""

    def __init__(self, agent: Agent, pipeline: EventPipeline) -> None:
        self.agents = {agent.id: agent}
        self.pipeline = pipeline
        self.sessions = {agent.id: {"Conversation 1": {"provider": "fake"}}}
        self.current = {agent.id: "Conversation 1"}
        self.sent_messages: list[tuple[str, str]] = []

    def session_name(self, agent_id) -> str | None:
        return self.current[agent_id]

    def session_names(self, agent_id) -> tuple[str, ...]:
        return tuple(self.sessions[agent_id])

    def state(self, agent_id) -> AgentState:
        return AgentState.IDLE

    def is_generating(self, agent_id) -> bool:
        return False

    async def send_message(self, agent_id, message: str, source: str = "user") -> None:
        self.sent_messages.append((message, source))
        await self.pipeline.enqueue(
            MessageSent(
                agent_id=agent_id,
                session_name=self.current[agent_id],
                message=message,
                source=source,
            )
        )

    async def create_session(self, agent_id, session_name: str) -> str:
        self.sessions[agent_id][session_name] = {"provider": "fake"}
        self.current[agent_id] = session_name
        await self.pipeline.enqueue(
            SessionCreated(
                agent_id=agent_id,
                session_name=session_name,
                identifier={"provider": "fake"},
            )
        )
        return session_name

    async def restore_session(self, agent_id, session_name: str, identifier_json) -> str:
        return await self._load_session(agent_id, session_name, identifier_json)

    async def _load_session(self, agent_id, session_name: str, identifier) -> str:
        self.sessions[agent_id][session_name] = identifier
        self.current[agent_id] = session_name
        await self.pipeline.enqueue(
            SessionUpdated(
                agent_id=agent_id,
                session_name=session_name,
                identifier=identifier,
            )
        )
        return session_name

    async def switch_session(self, agent_id, session_name: str) -> str:
        self.current[agent_id] = session_name
        return session_name

    async def interrupt(self, agent_id) -> bool:
        return False


def make_agent() -> Agent:
    return Agent(
        name="reviewer",
        sandbox=SandboxMode.READ_ONLY,
        workspace=".",
        model="test",
    )


def test_app_mounts_and_replays_session_updates() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        agent = make_agent()
        await pipeline.enqueue(
            SessionUpdated(
                agent_id=agent.id,
                session_name="Conversation 1",
                identifier={"provider": "fake"},
            )
        )
        repository = CountingSessionRepository()
        runner = FakeRunner(agent, pipeline)
        app = AgentChatApp(runner, repository)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.chats[agent.id].session_name == "Conversation 1"
            metadata = await app.session_repository.get_session(
                agent.id,
                "Conversation 1",
            )
            assert metadata.identifier == {"provider": "fake"}
            assert repository.save_count == 1
            await pipeline.enqueue(
                SessionUpdated(
                    agent_id=agent.id,
                    session_name="Conversation 1",
                    identifier={"provider": "fake", "turn": "updated"},
                )
            )
            assert repository.save_count == 2
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_app_renders_submitted_text_from_the_message_event() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent()
        runner = FakeRunner(agent, pipeline)
        await repository.save_session(
            agent.id,
            "Conversation 1",
            {"provider": "fake"},
        )
        app = AgentChatApp(runner, repository)

        async with app.run_test() as pilot:
            chat = app.chats[agent.id]
            chat.input.value = "Review this"
            await pilot.press("enter")
            await pilot.pause()

            assert runner.sent_messages == [("Review this", "user")]
            assert chat.transcript.entries == [
                TranscriptEntry("user", "Review this", EventDetailLevel.NORMAL, {"type": "message"})
            ]

            await pipeline.enqueue(
                MessageSent(
                    agent_id=agent.id,
                    session_name="Conversation 1",
                    message="Ready for review.",
                    source="planner",
                )
            )
            await pilot.pause()
            assert chat.transcript.entries[-1] == TranscriptEntry(
                "planner",
                "Ready for review.",
                EventDetailLevel.NORMAL,
                {"type": "message"},
            )

        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_app_registers_and_selects_sessions_created_outside_ui_commands() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent()
        runner = FakeRunner(agent, pipeline)
        await repository.save_session(
            agent.id,
            "Conversation 1",
            {"provider": "fake"},
        )
        app = AgentChatApp(runner, repository)

        async with app.run_test() as pilot:
            await runner.create_session(agent.id, "Created by MCP")
            await pilot.pause()

            assert app.chats[agent.id].session_name == "Created by MCP"
            assert (
                await repository.get_session(agent.id, "Created by MCP")
            ).identifier == {"provider": "fake"}

        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_app_logs_token_usage_in_the_event_session() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent()
        runner = FakeRunner(agent, pipeline)
        await repository.save_session(
            agent.id,
            "Conversation 1",
            {"provider": "fake"},
        )
        app = AgentChatApp(runner, repository)
        usage = TokenUsage(
            input_tokens=90,
            cached_input_tokens=40,
            output_tokens=10,
            reasoning_output_tokens=5,
            total_tokens=100,
        )

        async with app.run_test() as pilot:
            event = AgentTokenUsageUpdated(
                agent_id=agent.id,
                session_name="Conversation 1",
                last=usage,
                total=usage,
            )
            await pipeline.enqueue(event)
            await pilot.pause()

            entries = await repository.entries_for(agent.id, "Conversation 1")
            assert entries[-1].role == "usage"
            assert "100 total" in entries[-1].content
            assert app.active_token_usage is event

        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_active_token_usage_follows_the_selected_session() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent()
        runner = FakeRunner(agent, pipeline)
        runner.sessions[agent.id]["Conversation 2"] = {"provider": "fake-2"}
        await repository.save_session(
            agent.id,
            "Conversation 1",
            {"provider": "fake"},
        )
        await repository.save_session(
            agent.id,
            "Conversation 2",
            {"provider": "fake-2"},
        )
        app = AgentChatApp(runner, repository, transcribers=[])
        usage = TokenUsage(
            input_tokens=45,
            cached_input_tokens=10,
            output_tokens=5,
            reasoning_output_tokens=2,
            total_tokens=50,
        )
        second_session_usage = AgentTokenUsageUpdated(
            agent_id=agent.id,
            session_name="Conversation 2",
            last=usage,
            total=usage,
        )

        async with app.run_test() as pilot:
            await pipeline.enqueue(second_session_usage)
            await pilot.pause()

            assert app.active_token_usage is None

            await app.chats[agent.id].select_session("Conversation 2")
            assert app.active_token_usage is second_session_usage

        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_constructor_transcriber_collection_replaces_the_defaults() -> None:
    class CustomMessageTranscriber(MessageSentTranscriber):
        def _write(self, event: MessageSent) -> RoutedTranscriptEntry:
            return RoutedTranscriptEntry(
                event.agent_id,
                event.session_name,
                TranscriptEntry(
                    "system",
                    f"custom: {event.message}",
                    EventDetailLevel.NORMAL,
                    {"type": "system"},
                ),
            )

    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent()
        runner = FakeRunner(agent, pipeline)
        await repository.save_session(
            agent.id,
            "Conversation 1",
            {"provider": "fake"},
        )
        app = AgentChatApp(
            runner,
            repository,
            transcribers=[CustomMessageTranscriber()],
        )

        async with app.run_test() as pilot:
            await pipeline.enqueue(
                MessageSent(
                    agent_id=agent.id,
                    session_name="Conversation 1",
                    message="hello",
                )
            )
            usage = TokenUsage(
                input_tokens=1,
                cached_input_tokens=0,
                output_tokens=1,
                reasoning_output_tokens=0,
                total_tokens=2,
            )
            token_event = AgentTokenUsageUpdated(
                agent_id=agent.id,
                session_name="Conversation 1",
                last=usage,
                total=usage,
            )
            await pipeline.enqueue(token_event)
            await pilot.pause()

            entries = await repository.entries_for(agent.id, "Conversation 1")
            assert [entry.content for entry in entries] == ["custom: hello"]
            assert set(app.chats[agent.id]._transcribers) == {"message"}
            assert app.active_token_usage is token_event

        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_constructor_rejects_duplicate_transcriber_registrations() -> None:
    class ConflictingTranscriptTranscriber(AgentErrorTranscriber):
        def __init__(self) -> None:
            Transcriber.__init__(self, AgentError, "message")

    with pytest.raises(ValueError, match="event MessageSent"):
        AgentChatApp._build_transcriber_registries(
            [MessageSentTranscriber(), MessageSentTranscriber()]
        )

    with pytest.raises(ValueError, match="transcript type 'message'"):
        AgentChatApp._build_transcriber_registries(
            [MessageSentTranscriber(), ConflictingTranscriptTranscriber()]
        )


def test_each_transcript_mutation_has_an_independent_pipeline_path() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent()
        runner = FakeRunner(agent, pipeline)
        await repository.save_session(
            agent.id,
            "Conversation 1",
            {"provider": "fake"},
        )
        app = AgentChatApp(runner, repository)
        entry = TranscriptEntry(
            "system",
            "initial",
            EventDetailLevel.NORMAL,
            {"type": "system"},
        )

        async with app.run_test() as pilot:
            await pipeline.enqueue(
                TranscriptEntryAdded(agent_id=agent.id, session_name="Conversation 1", entry=entry)
            )
            await pilot.pause()
            assert app.chats[agent.id].transcript.entries == [entry]

            updated = replace(entry, content="updated")
            await pipeline.enqueue(
                TranscriptEntryUpdated(
                    agent_id=agent.id,
                    session_name="Conversation 1",
                    entry=updated,
                )
            )
            await pilot.pause()
            assert app.chats[agent.id].transcript.entries == [updated]

            await pipeline.enqueue(
                TranscriptEntryDeleted(
                    agent_id=agent.id,
                    session_name="Conversation 1",
                    entry_id=entry.entry_id,
                )
            )
            await pilot.pause()
            assert app.chats[agent.id].transcript.entries == []
            assert await repository.entries_for(agent.id, "Conversation 1") == ()

        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_permission_transcriber_updates_the_request_entry_in_place() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = make_agent()
        runner = FakeRunner(agent, pipeline)
        await repository.save_session(
            agent.id,
            "Conversation 1",
            {"provider": "fake"},
        )
        responses: list[PermissionResponse] = []

        def capture(event: PermissionResponse) -> PermissionResponse:
            responses.append(event)
            return event

        pipeline.add_listener(PermissionResponse, capture)
        app = AgentChatApp(runner, repository)

        async with app.run_test() as pilot:
            await pipeline.enqueue(
                PermissionRequest(
                    request_id="approval-1",
                    agent_id=agent.id,
                    session_name="Conversation 1",
                    request_type=PermissionRequestType.COMMAND_EXECUTION,
                    request={"command": "pytest"},
                )
            )
            await pilot.pause()

            card = app.query_one(PermissionRequestCard)
            next(button for button in card.query(Button) if button.name == "accept").press()
            await pilot.pause()

            entries = await repository.entries_for(agent.id, "Conversation 1")
            assert len(entries) == 1
            assert entries[0].metadata["status"] == "resolved"
            assert entries[0].metadata["decision"] == "accept"
            assert len(responses) == 1

        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())
