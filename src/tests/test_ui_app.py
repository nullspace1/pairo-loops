"""Headless smoke test for the async Textual integration."""

import asyncio

from pairo.core.agent import AgentState
from pairo.core.agent_config import AgentConfiguration, JsonObject, Sandbox
from pairo.core.events import EventPipeline, MessageSent, SessionUpdated
from pairo.ui.app import AgentChatApp
from pairo.ui.models import EventDetailLevel, TranscriptEntry
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


class CountingSessionRepository(SqliteSessionRepository):
    def __init__(self) -> None:
        super().__init__()
        self.save_count = 0

    async def save_session(self, agent_name, session_name, identifier):
        self.save_count += 1
        return await super().save_session(agent_name, session_name, identifier)


class FakeAgent:
    name = "reviewer"
    description = None
    session_name = "Conversation 1"
    session_names = ("Conversation 1",)
    state = AgentState.IDLE
    is_generating = False
    configured_mcp_servers = ()
    configuration = AgentConfiguration(
        sandbox=Sandbox.READ_ONLY,
        workspace=".",
        model="test",
    )

    def __init__(self, pipeline: EventPipeline) -> None:
        self._pipeline = pipeline
        self.sent_messages: list[tuple[str, str]] = []

    async def send_message(self, message: str, source: str = "user") -> None:
        self.sent_messages.append((message, source))
        await self._pipeline.publish(
            MessageSent(
                agent_name=self.name,
                session_name=self.session_name,
                message=message,
                source=source,
            )
        )

    async def create_session(self, session_name: str) -> str:
        return session_name

    async def restore_session(self, session_name: str, identifier_json: JsonObject) -> str:
        return session_name

    async def switch_session(self, session_name: str) -> str:
        return session_name

    async def cancel_generation(self) -> bool:
        return False


def test_app_mounts_and_replays_session_updates() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        await pipeline.publish(
            SessionUpdated(
                agent_name="reviewer",
                session_name="Conversation 1",
                identifier={"provider": "fake"},
            )
        )
        repository = CountingSessionRepository()
        app = AgentChatApp([FakeAgent(pipeline)], pipeline, repository)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.chats["reviewer"].session_name == "Conversation 1"
            metadata = await app.session_repository.get_session(
                "reviewer",
                "Conversation 1",
            )
            assert metadata.identifier == {"provider": "fake"}
            assert repository.save_count == 1
            await pipeline.publish(
                SessionUpdated(
                    agent_name="reviewer",
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
        agent = FakeAgent(pipeline)
        await repository.save_session(
            "reviewer",
            "Conversation 1",
            {"provider": "fake"},
        )
        app = AgentChatApp([agent], pipeline, repository)

        async with app.run_test() as pilot:
            chat = app.chats["reviewer"]
            chat.input.value = "Review this"
            await pilot.press("enter")
            await pilot.pause()

            assert agent.sent_messages == [("Review this", "user")]
            assert chat.transcript.entries == [
                TranscriptEntry("user", "Review this", EventDetailLevel.NORMAL, {"type": "message"})
            ]

            await pipeline.publish(
                MessageSent(
                    agent_name="reviewer",
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
