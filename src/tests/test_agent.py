"""Behavioral tests for the provider-neutral async agent."""

import asyncio

from pydantic import ConfigDict

from pairo.core.agent import Agent, AgentState
from pairo.core.agent_config import AgentConfiguration, JsonObject, Sandbox
from pairo.core.agent_server import SessionIdentifier, SessionNotification
from pairo.core.events import (
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

    async def new_session(
        self,
        configuration: AgentConfiguration,
        agent_name: str,
    ) -> SessionIdentifier:
        self.calls.append(("new", configuration.model, agent_name))
        return FakeIdentifier(revision=0)

    async def restore_session(self, configuration: AgentConfiguration, identifier_json: JsonObject) -> SessionIdentifier:
        self.calls.append(("restore", identifier_json))
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
                session=updated,
                event=ClientTextDelta(text="deltas"),
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

    async def close(self, session: SessionIdentifier) -> None:
        self.calls.append(("close",))


def configuration() -> AgentConfiguration:
    return AgentConfiguration(
        sandbox=Sandbox.READ_ONLY,
        workspace=".",
        model="test-model",
    )


def test_agent_uses_opaque_identifiers_and_async_lifecycle() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        client = FakeClient()
        starts_when_user_message_published: list[int] = []

        def record_client_state(event: MessageSent) -> MessageSent:
            if event.source == "user":
                starts_when_user_message_published.append(
                    sum(call[0] == "start" for call in client.calls)
                )
            return event

        pipeline.add_listener(MessageSent, record_client_state)
        agent = Agent(
            name="researcher",
            config=configuration(),
            client=client,
            event_pipeline=pipeline,
        )

        assert await agent.run("Conversation 1") == "Conversation 1"
        await agent.send_message("Review this")
        assert await agent.wait_for_message(timeout=1) == "Finished"
        await asyncio.sleep(0)
        assert agent.state is AgentState.IDLE
        assert agent.session_identifier_json("Conversation 1")["revision"] == 2
        updates = [event for event in pipeline.events if isinstance(event, SessionUpdated)]
        assert [event.identifier["revision"] for event in updates] == [0, 1, 2]
        assert client.calls.count(("notifications",)) == 1
        assert ("new", "test-model", "researcher") in client.calls
        assert starts_when_user_message_published == [0]
        assert MessageSent(
            agent_name="researcher",
            session_name="Conversation 1",
            message="Review this",
            source="user",
        ) in pipeline.events
        assert MessageSent(
            agent_name="researcher",
            session_name="Conversation 1",
            message="Finished",
            source="researcher",
        ) in pipeline.events

        await agent.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_agent_publishes_the_supplied_message_source() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        client = FakeClient()
        agent = Agent(
            name="researcher",
            config=configuration(),
            client=client,
            event_pipeline=pipeline,
        )

        await agent.run("Conversation 1")
        await agent.send_message("Review this", source="planner")

        assert MessageSent(
            agent_name="researcher",
            session_name="Conversation 1",
            message="Review this",
            source="planner",
        ) in pipeline.events

        await agent.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_restore_and_duplicate_session_validation() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        client = FakeClient()
        agent = Agent(name="a", config=configuration(), client=client, event_pipeline=pipeline)
        await agent.run("First")
        await agent.restore_session("Saved", {"provider": "fake", "revision": 7})
        assert agent.session_name == "Saved"
        assert ("restore", {"provider": "fake", "revision": 7}) in client.calls
        try:
            await agent.create_session("Saved")
        except ValueError as error:
            assert "already exists" in str(error)
        else:
            raise AssertionError("duplicate session name should fail")
        await agent.close()

    asyncio.run(scenario())
