"""Contract tests for the in-memory fake client."""

import asyncio
from pathlib import Path

from pairo.core.agent import Agent, SandboxMode
from pairo.core.clients import FakeAgentClient
from pairo.core.events import ClientResponseCompleted, ClientResponseInterrupted


def test_fake_client_returns_a_new_response_after_each_message() -> None:
    responses = iter(("first-random-value", "second-random-value"))

    async def scenario() -> None:
        client = FakeAgentClient(response_factory=lambda: next(responses))
        agent = Agent(
            name="fake",
            sandbox=SandboxMode.READ_ONLY,
            workspace=Path.cwd(),
            model="fake",
        )
        session = await client.new_session(agent)

        first_turn = await client.start(session, "hello")
        first = [item async for item in client.notifications(first_turn)]
        second_turn = await client.start(first[0].session, "hello again")
        second = [item async for item in client.notifications(second_turn)]

        assert isinstance(first[0].event, ClientResponseCompleted)
        assert first[0].event.message == "first-random-value"
        assert isinstance(second[0].event, ClientResponseCompleted)
        assert second[0].event.message == "second-random-value"
        assert first[0].session.turn_id is None
        assert second[0].session.turn_id is None

    asyncio.run(scenario())


def test_fake_client_interrupts_an_active_turn() -> None:
    async def scenario() -> None:
        client = FakeAgentClient(response_factory=lambda: "unused")
        agent = Agent(
            name="fake",
            sandbox=SandboxMode.READ_ONLY,
            workspace=Path.cwd(),
            model="fake",
        )
        session = await client.new_session(agent)
        turn = await client.start(session, "hello")

        await client.interrupt(turn)
        notifications = [item async for item in client.notifications(turn)]

        assert isinstance(notifications[0].event, ClientResponseInterrupted)
        assert notifications[0].session.turn_id is None

    asyncio.run(scenario())
