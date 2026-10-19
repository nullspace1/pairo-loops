"""Tests for the high-level asynchronous agent application lifecycle."""

import asyncio
from pathlib import Path

import pytest

from pairo import run_agents
from pairo import runtime
from pairo.core.agent import AgentConfiguration, SandboxPermission
from pairo.core.clients import ActiveSession, FakeSessionIdentifier
from pairo.core.events import EventPipeline


class StubAgent:
    def __init__(
        self,
        calls: list[str],
        workspace: Path,
        *,
        start_error: Exception | None = None,
    ) -> None:
        self.calls = calls
        self._configuration = AgentConfiguration(
            name="agent",
            sandbox=SandboxPermission.READ_ONLY,
            workspace=workspace,
            model="test",
        )
        self.start_error = start_error

    def configuration(self):
        return self._configuration

    async def new_session(self, name: str):
        self.calls.append(f"start:{name}")
        if self.start_error is not None:
            raise self.start_error
        return ActiveSession(
            name=name,
            identifier=FakeSessionIdentifier(session_id="session-1"),
        )

    async def close(self) -> None:
        self.calls.append("close:agent")


class StubRepository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def save_session(self, _agent_id, name, _identifier) -> None:
        self.calls.append(f"save:{name}")

    async def close(self) -> None:
        self.calls.append("close:repository")


def test_run_agents_manages_complete_application_lifecycle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        pipeline = EventPipeline()
        agent = StubAgent(calls, tmp_path)
        agents = {agent.configuration().id: agent}
        repository = StubRepository(calls)

        async def fake_run_ui(received_agents, received_pipeline, received_repository):
            assert received_agents == agents
            assert received_pipeline is pipeline
            assert received_repository is repository
            calls.append("ui")

        monkeypatch.setattr(runtime, "run_ui", fake_run_ui)
        await run_agents(agents, pipeline, repository, initial_session_name="Initial")

        assert calls == [
            "start:Initial",
            "save:Initial",
            "ui",
            "close:agent",
            "close:repository",
        ]
        assert not hasattr(pipeline, "_closed")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("ui_error", "start_error", "expected"),
    [
        (RuntimeError("UI failed"), None, "UI failed"),
        (None, RuntimeError("startup failed"), "startup failed"),
    ],
)
def test_run_agents_releases_resources_after_failures(
    monkeypatch,
    tmp_path: Path,
    ui_error: Exception | None,
    start_error: Exception | None,
    expected: str,
) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        pipeline = EventPipeline()
        agent = StubAgent(calls, tmp_path, start_error=start_error)
        repository = StubRepository(calls)

        async def fake_run_ui(*_args):
            if ui_error is not None:
                raise ui_error
            raise AssertionError("UI should not run after startup failure")

        monkeypatch.setattr(runtime, "run_ui", fake_run_ui)
        with pytest.raises(RuntimeError, match=expected):
            await run_agents(
                {agent.configuration().id: agent},
                pipeline,
                repository,
            )

        assert "close:agent" in calls
        assert calls[-1] == "close:repository"
        assert not hasattr(pipeline, "_closed")

    asyncio.run(scenario())
