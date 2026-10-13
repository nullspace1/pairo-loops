"""Headless smoke test for the async Textual integration."""

import asyncio

from pairo.core.agent import Agent, AgentState, SandboxMode
from pairo.core.events import EventPipeline, MessageSent, SessionUpdated
from pairo.ui.app import AgentChatApp
from pairo.ui.models import EventDetailLevel, TranscriptEntry
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


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
        await self.pipeline.publish(
            MessageSent(
                agent_id=agent_id,
                session_name=self.current[agent_id],
                message=message,
                source=source,
            )
        )

    async def create_session(self, agent_id, session_name: str) -> str:
        return await self._load_session(agent_id, session_name, {"provider": "fake"})

    async def restore_session(self, agent_id, session_name: str, identifier_json) -> str:
        return await self._load_session(agent_id, session_name, identifier_json)

    async def _load_session(self, agent_id, session_name: str, identifier) -> str:
        self.sessions[agent_id][session_name] = identifier
        self.current[agent_id] = session_name
        await self.pipeline.publish(
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
        await pipeline.publish(
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
            await pipeline.publish(
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

            await pipeline.publish(
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
