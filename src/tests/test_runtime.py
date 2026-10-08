"""Tests for the high-level agent application lifecycle."""

import asyncio

import pytest

from pairo import run_agents
from pairo.core.events import EventPipeline
from pairo import runtime


class FakeAgent:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def run(self, initial_session_name: str) -> str:
        self.calls.append(f"run:{self.name}:{initial_session_name}")
        return initial_session_name

    async def close(self) -> None:
        self.calls.append(f"close:{self.name}")


class FakeRepository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def close(self) -> None:
        self.calls.append("close:repository")


def test_run_agents_manages_the_complete_application_lifecycle(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        pipeline = EventPipeline()
        repository = FakeRepository(calls)
        agents = (FakeAgent("planner", calls), FakeAgent("developer", calls))

        async def fake_run_ui(received_agents, received_pipeline, received_repository):
            assert received_agents == agents
            assert received_pipeline is pipeline
            assert received_repository is repository
            calls.append("ui")

        monkeypatch.setattr(runtime, "run_ui", fake_run_ui)

        await run_agents(agents, pipeline, repository, initial_session_name="Initial")

        assert set(calls[:2]) == {"run:planner:Initial", "run:developer:Initial"}
        assert calls[2:] == ["ui", "close:planner", "close:developer", "close:repository"]
        assert pipeline._closed

    asyncio.run(scenario())


def test_run_agents_releases_resources_when_the_ui_fails(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        pipeline = EventPipeline()
        repository = FakeRepository(calls)
        agent = FakeAgent("planner", calls)

        async def failing_run_ui(*_args):
            raise RuntimeError("UI failed")

        monkeypatch.setattr(runtime, "run_ui", failing_run_ui)

        with pytest.raises(RuntimeError, match="UI failed"):
            await run_agents((agent,), pipeline, repository)

        assert calls == [
            "run:planner:Conversation 1",
            "close:planner",
            "close:repository",
        ]
        assert pipeline._closed

    asyncio.run(scenario())


def test_run_agents_rejects_an_empty_agent_collection() -> None:
    async def scenario() -> None:
        with pytest.raises(ValueError, match="at least one agent"):
            await run_agents((), EventPipeline(), FakeRepository([]))

    asyncio.run(scenario())


def test_run_agents_closes_every_resource_when_startup_fails(monkeypatch) -> None:
    class FailingAgent(FakeAgent):
        async def run(self, initial_session_name: str) -> str:
            self.calls.append(f"run:{self.name}:{initial_session_name}")
            raise RuntimeError("startup failed")

    async def scenario() -> None:
        calls: list[str] = []
        pipeline = EventPipeline()
        repository = FakeRepository(calls)
        failing = FailingAgent("broken", calls)
        healthy = FakeAgent("healthy", calls)

        async def ui_must_not_run(*_args):
            raise AssertionError("UI should not start")

        monkeypatch.setattr(runtime, "run_ui", ui_must_not_run)
        with pytest.raises(RuntimeError, match="startup failed"):
            await run_agents((failing, healthy), pipeline, repository)

        assert "close:broken" in calls
        assert "close:healthy" in calls
        assert calls[-1] == "close:repository"
        assert pipeline._closed

    asyncio.run(scenario())
