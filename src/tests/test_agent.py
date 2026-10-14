"""Behavioral tests for the provider-neutral Runner."""

import asyncio
from pathlib import Path

from pydantic import ConfigDict

from pairo.core.agent import Agent, JsonObject, Runner, SandboxMode, SessionIdentifier
from pairo.core.clients.client import SessionNotification
from pairo.core.events import (
    AgentResponseCompleted,
    AgentTextDelta,
    ClientResponseCompleted,
    ClientTextDelta,
    EventPipeline,
    MessageSent,
    SessionUpdated,
)


class FakeIdentifier(SessionIdentifier):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = "fake"
    revision: int


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self._notifications: asyncio.Queue[SessionNotification] = asyncio.Queue()

    async def new_session(self, agent: Agent) -> SessionIdentifier:
        self.calls.append(("new", agent.model, agent.name))
        return FakeIdentifier(revision=0)

    async def restore_session(
        self,
        agent: Agent,
        identifier_json: JsonObject,
    ) -> SessionIdentifier:
        self.calls.append(("restore", agent.name, identifier_json))
        return FakeIdentifier.model_validate(identifier_json)

    async def start(self, session: SessionIdentifier, message: str) -> SessionIdentifier:
        current = FakeIdentifier.model_validate(session.model_dump())
        self.calls.append(("start", current.revision, message))
        updated = current.model_copy(update={"revision": current.revision + 1})
        await self._notifications.put(
            SessionNotification(
                session=updated,
                event=ClientTextDelta(text="Frequent "),
            )
        )
        await self._notifications.put(
            SessionNotification(
                session=updated.model_copy(update={"revision": updated.revision + 1}),
                event=ClientResponseCompleted(message="Finished"),
            )
        )
        return updated

    async def steer(self, session: SessionIdentifier, message: str) -> SessionIdentifier:
        current = FakeIdentifier.model_validate(session.model_dump())
        self.calls.append(("steer", message))
        return current.model_copy(update={"revision": current.revision + 1})

    async def interrupt(self, session: SessionIdentifier) -> SessionIdentifier:
        self.calls.append(("interrupt",))
        return session

    async def notifications(self, session: SessionIdentifier):
        self.calls.append(("notifications",))
        while True:
            notification = await self._notifications.get()
            yield notification
            if isinstance(notification.event, ClientResponseCompleted):
                return

    async def close(self) -> None:
        self.calls.append(("close",))


def configured_agent(tmp_path: Path, name: str = "researcher") -> Agent:
    return Agent(
        name=name,
        sandbox=SandboxMode.READ_ONLY,
        workspace=tmp_path,
        model="test-model",
    )


def test_runner_uses_opaque_identifiers_and_async_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        client = FakeClient()
        agent = configured_agent(tmp_path)
        runner = Runner([agent], pipeline, client)
        starts_when_user_message_published: list[int] = []

        def record_client_state(event: MessageSent) -> MessageSent:
            if event.source == "user":
                starts_when_user_message_published.append(
                    sum(call[0] == "start" for call in client.calls)
                )
            return event

        pipeline.add_listener(MessageSent, record_client_state)
        await runner.start()
        await runner.send_message(agent.id, "Review this")
        assert await runner.wait_for_message(agent.id, timeout=1) == "Finished"
        await asyncio.sleep(0)

        assert runner.session_identifier_json(agent.id, "Conversation 1")["revision"] == 2
        updates = [event for event in pipeline.events if isinstance(event, SessionUpdated)]
        assert [event.identifier["revision"] for event in updates] == [0, 1, 2]
        assert client.calls.count(("notifications",)) == 1
        assert ("new", "test-model", "researcher") in client.calls
        assert starts_when_user_message_published == [0]
        assert AgentTextDelta(
            agent_id=agent.id,
            session_name="Conversation 1",
            text="Frequent ",
        ) in pipeline.events
        assert AgentResponseCompleted(
            agent_id=agent.id,
            session_name="Conversation 1",
            message="Finished",
        ) in pipeline.events
        assert MessageSent(
            agent_id=agent.id,
            session_name="Conversation 1",
            message="Finished",
            source="researcher",
        ) in pipeline.events

        await runner.close()
        await runner.close()
        assert client.calls.count(("close",)) == 1
        await pipeline.close()

    asyncio.run(scenario())


def test_runner_publishes_supplied_message_source(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        agent = configured_agent(tmp_path)
        runner = Runner([agent], pipeline, FakeClient())
        await runner.start()
        await runner.send_message(agent.id, "Review this", source="planner")
        assert MessageSent(
            agent_id=agent.id,
            session_name="Conversation 1",
            message="Review this",
            source="planner",
        ) in pipeline.events
        await runner.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_runner_accepts_duplicate_agent_names_and_routes_by_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        first = configured_agent(tmp_path, "shared")
        second = configured_agent(tmp_path, "shared")
        pipeline = EventPipeline()
        runner = Runner([first, second], pipeline, FakeClient())

        await runner.start()

        assert tuple(runner.agents) == (first.id, second.id)
        updates = [event for event in pipeline.events if isinstance(event, SessionUpdated)]
        assert {event.agent_id for event in updates} == {first.id, second.id}
        assert runner.session_name(first.id) == "Conversation 1"
        assert runner.session_name(second.id) == "Conversation 1"

        await runner.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_restore_switch_and_duplicate_session_validation(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        client = FakeClient()
        agent = configured_agent(tmp_path, "a")
        runner = Runner([agent], pipeline, client)
        await runner.start("First")
        await runner.restore_session(
            agent.id,
            "Saved",
            {"provider": "fake", "revision": 7},
        )
        assert runner.session_name(agent.id) == "Saved"
        assert ("restore", "a", {"provider": "fake", "revision": 7}) in client.calls
        assert await runner.switch_session(agent.id, "First") == "First"
        try:
            await runner.create_session(agent.id, "Saved")
        except ValueError as error:
            assert "already exists" in str(error)
        else:
            raise AssertionError("duplicate session name should fail")
        await runner.close()
        await pipeline.close()

    asyncio.run(scenario())
