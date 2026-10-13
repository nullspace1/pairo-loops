"""Unit coverage for the per-turn interrupt state machine."""

import asyncio

from pydantic import ConfigDict

from pairo import InterruptState
from pairo.core.agent.run import AgentRun
from pairo.core.agent.session_identifier import SessionIdentifier


class InterruptIdentifier(SessionIdentifier):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = "interrupt-test"


def test_pending_interrupt_is_claimed_once_after_turn_start() -> None:
    async def scenario() -> None:
        run = AgentRun()
        await run.set_session("Main", InterruptIdentifier())
        await run.enqueue("hello")

        requested, identifier = await run.request_interrupt()
        assert requested
        assert identifier is None
        assert run.interrupt_state is InterruptState.PENDING

        run.turn_started.set()
        assert await run.claim_pending_interrupt()
        assert not await run.claim_pending_interrupt()
        assert run.interrupt_state is InterruptState.SENT

        await run.finish_turn()
        assert run.interrupt_state is InterruptState.NONE

    asyncio.run(scenario())


def test_started_turn_interrupt_is_dispatched_immediately_once() -> None:
    async def scenario() -> None:
        run = AgentRun()
        identifier = InterruptIdentifier()
        await run.set_session("Main", identifier)
        await run.enqueue("hello")
        run.turn_started.set()

        requested, claimed_identifier = await run.request_interrupt()
        assert requested
        assert claimed_identifier is identifier
        assert run.interrupt_state is InterruptState.SENT

        requested_again, duplicate_identifier = await run.request_interrupt()
        assert requested_again
        assert duplicate_identifier is None
        assert run.interrupt_state is InterruptState.SENT

    asyncio.run(scenario())
