"""In-memory AgentClient implementation for demos and tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from secrets import token_urlsafe
from typing import Literal
from uuid import uuid4

from pydantic import ConfigDict

from ..agent.agent import Agent
from ..agent.json_object import JsonObject
from ..agent.session_identifier import SessionIdentifier
from ..events import ClientResponseCompleted, ClientResponseInterrupted
from .client import SessionNotification


class FakeSessionIdentifier(SessionIdentifier):
    """Serializable conversation state owned by the fake provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["fake"] = "fake"
    session_id: str
    turn_id: str | None = None


class FakeAgentClient:
    """Return a newly generated string for each message without external I/O."""

    def __init__(self, response_factory: Callable[[], str] | None = None) -> None:
        # Injection keeps tests deterministic while the production default is random.
        self._response_factory = response_factory or token_urlsafe
        self._responses: dict[tuple[str, str], str] = {}
        self._interrupted_turns: set[tuple[str, str]] = set()
        self._session_ids: set[str] = set()
        self._closed = False

    async def new_session(self, agent: Agent) -> SessionIdentifier:
        del agent  # The fake does not need provider configuration.
        self._require_open()
        identifier = FakeSessionIdentifier(session_id=str(uuid4()))
        self._session_ids.add(identifier.session_id)
        return identifier

    async def restore_session(
        self,
        agent: Agent,
        identifier_json: JsonObject,
    ) -> SessionIdentifier:
        del agent
        self._require_open()
        identifier = FakeSessionIdentifier.model_validate(identifier_json)
        restored = identifier.model_copy(update={"turn_id": None})
        self._session_ids.add(restored.session_id)
        return restored

    async def start(
        self,
        session: SessionIdentifier,
        message: str,
    ) -> SessionIdentifier:
        del message  # Any message produces the same kind of random fake response.
        identifier = self._registered_identifier(session)
        if identifier.turn_id is not None:
            raise ValueError("The fake session already has an active turn.")
        updated = identifier.model_copy(update={"turn_id": str(uuid4())})
        self._responses[self._turn_key(updated)] = self._response_factory()
        return updated

    async def steer(
        self,
        session: SessionIdentifier,
        message: str,
    ) -> SessionIdentifier:
        del message
        identifier = self._active_identifier(session)
        self._responses[self._turn_key(identifier)] = self._response_factory()
        return identifier

    async def compact(self, session: SessionIdentifier) -> None:
        self._registered_identifier(session)

    async def interrupt(self, session: SessionIdentifier) -> SessionIdentifier:
        identifier = self._active_identifier(session)
        self._interrupted_turns.add(self._turn_key(identifier))
        return identifier

    async def notifications(
        self,
        session: SessionIdentifier,
    ) -> AsyncIterator[SessionNotification]:
        identifier = self._active_identifier(session)
        turn_key = self._turn_key(identifier)
        response = self._responses.pop(turn_key, None)
        if response is None:
            raise ValueError("The fake turn has no pending response.")

        completed = identifier.model_copy(update={"turn_id": None})
        if turn_key in self._interrupted_turns:
            self._interrupted_turns.remove(turn_key)
            event = ClientResponseInterrupted()
        else:
            event = ClientResponseCompleted(message=response)
        yield SessionNotification(session=completed, event=event)

    async def close(self) -> None:
        self._closed = True
        self._responses.clear()
        self._interrupted_turns.clear()
        self._session_ids.clear()

    def _registered_identifier(
        self,
        session: SessionIdentifier,
    ) -> FakeSessionIdentifier:
        self._require_open()
        identifier = FakeSessionIdentifier.model_validate(
            session.model_dump(mode="json")
        )
        if identifier.session_id not in self._session_ids:
            raise ValueError(
                f"Fake session '{identifier.session_id}' is not registered."
            )
        return identifier

    def _active_identifier(
        self,
        session: SessionIdentifier,
    ) -> FakeSessionIdentifier:
        identifier = self._registered_identifier(session)
        if identifier.turn_id is None:
            raise ValueError("The fake session has no active turn.")
        return identifier

    @staticmethod
    def _turn_key(identifier: FakeSessionIdentifier) -> tuple[str, str]:
        if identifier.turn_id is None:
            raise ValueError("The fake session has no active turn.")
        return identifier.session_id, identifier.turn_id

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("The fake client is closed.")


__all__ = ["FakeAgentClient", "FakeSessionIdentifier"]
