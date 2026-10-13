"""Tests for the high-level Runner application lifecycle."""

import asyncio

import pytest

from pairo import run_agents
from pairo.core.events import EventPipeline
from pairo import runtime


class FakeRunner:
    def __init__(self, calls: list[str], *, start_error: Exception | None = None) -> None:
        self.calls = calls
        self.pipeline = EventPipeline()
        self.start_error = start_error

    async def start(self, initial_session_name: str) -> None:
        self.calls.append(f"start:{initial_session_name}")
        if self.start_error is not None:
            raise self.start_error

    async def close(self) -> None:
        self.calls.append("close:runner")


class FakeRepository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def close(self) -> None:
        self.calls.append("close:repository")


def test_run_agents_manages_complete_application_lifecycle(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        runner = FakeRunner(calls)
        repository = FakeRepository(calls)

        async def fake_run_ui(received_runner, received_repository):
            assert received_runner is runner
            assert received_repository is repository
            calls.append("ui")

        monkeypatch.setattr(runtime, "run_ui", fake_run_ui)
        await run_agents(runner, repository, initial_session_name="Initial")

        assert calls == [
            "start:Initial",
            "ui",
            "close:runner",
            "close:repository",
        ]
        assert runner.pipeline._closed

    asyncio.run(scenario())


def test_run_agents_releases_resources_when_ui_fails(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        runner = FakeRunner(calls)
        repository = FakeRepository(calls)

        async def failing_run_ui(*_args):
            raise RuntimeError("UI failed")

        monkeypatch.setattr(runtime, "run_ui", failing_run_ui)
        with pytest.raises(RuntimeError, match="UI failed"):
            await run_agents(runner, repository)
        assert calls == [
            "start:Conversation 1",
            "close:runner",
            "close:repository",
        ]
        assert runner.pipeline._closed

    asyncio.run(scenario())


def test_run_agents_closes_resources_when_startup_fails(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        runner = FakeRunner(calls, start_error=RuntimeError("startup failed"))
        repository = FakeRepository(calls)

        async def ui_must_not_run(*_args):
            raise AssertionError("UI should not start")

        monkeypatch.setattr(runtime, "run_ui", ui_must_not_run)
        with pytest.raises(RuntimeError, match="startup failed"):
            await run_agents(runner, repository)
        assert calls == [
            "start:Conversation 1",
            "close:runner",
            "close:repository",
        ]
        assert runner.pipeline._closed

    asyncio.run(scenario())
