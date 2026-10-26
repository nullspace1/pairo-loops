"""Tests for the high-level asynchronous agent application lifecycle."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from pairo import run_agents
from pairo import runtime
from pairo.core.agent import AgentConfiguration, SandboxPermission
from pairo.core.clients import ActiveSession, AgentStatus, FakeAgent, FakeSessionIdentifier
from pairo.core.events import EventPipeline
from pairo.ui import SlashCommand, app as ui_app
from pairo.ui.app import AgentChatApp, UserInterfaceLauncher
from pairo.ui.configuration import UserInterfaceConfiguration
from pairo.ui.transcribers import default_transcribers
from pairo.ui.widgets.sidebar_statistics import default_sidebar_statistics
from pairo.ui.sqlite_session_repository import SqliteSessionRepository
from pairo.ui.navigation_events import AgentSwitchRequested


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
            id=uuid4(),
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


class TrackingFakeAgent(FakeAgent):
    """Record shutdown ownership while retaining the real UI-facing agent behavior."""

    def __init__(self, configuration: AgentConfiguration, pipeline: EventPipeline) -> None:
        super().__init__(configuration, pipeline, lambda: "done")
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        await super().close()


class TrackingRepository(SqliteSessionRepository):
    """Record that the runtime, rather than the UI, closes an injected repository."""

    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        await super().close()


class QuittingTextualLauncher:
    """Exercise Textual's quit action instead of simulating a launcher return."""

    def __init__(self) -> None:
        self.returned = False

    async def __call__(self, agents, pipeline, repository) -> None:
        app = AgentChatApp(
            agents, pipeline, repository,
            transcribers=default_transcribers(),
            sidebar_statistics={agent_id: default_sidebar_statistics(agent) for agent_id, agent in agents.items()},
            commands=(),
        )

        async def quit_after_mount(pilot) -> None:
            await pilot.pause()
            await pilot.press("ctrl+q")

        await app.run_async(headless=True, auto_pilot=quit_after_mount)
        self.returned = True


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


def test_textual_quit_returns_run_agents_and_releases_idle_resources(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A real Textual quit must not leave its host event loop alive."""

    async def scenario() -> None:
        pipeline = EventPipeline()
        configuration = AgentConfiguration(
            id=uuid4(),
            name="agent",
            sandbox=SandboxPermission.READ_ONLY,
            workspace=tmp_path,
            model="test",
        )
        agent = TrackingFakeAgent(configuration, pipeline)
        repository = TrackingRepository()
        launcher = QuittingTextualLauncher()
        monkeypatch.setattr(runtime, "run_ui", launcher)

        await asyncio.wait_for(
            run_agents({configuration.id: agent}, pipeline, repository),
            timeout=1,
        )

        assert launcher.returned
        assert agent.close_calls == 1
        assert agent.status() is AgentStatus.INACTIVE
        assert repository.close_calls == 1
        assert not [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("event-subscription-")
        ]

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


class LauncherCommand(SlashCommand):
    name = "/launcher"

    async def invoke(self, event_pipeline, agent, agents, session_repository, argument) -> None:
        event_pipeline.push(AgentSwitchRequested(agent_id=agent.configuration().id))


def test_ui_launcher_composes_default_commands_and_owns_only_its_repository(
    monkeypatch, tmp_path: Path
) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        agent = TrackingFakeAgent(
            AgentConfiguration(
                id=uuid4(), name="agent", sandbox=SandboxPermission.READ_ONLY,
                workspace=tmp_path, model="test",
            ), pipeline,
        )
        repository = TrackingRepository()
        observed: list[str] = []

        async def fake_run_async(app):
            await app._controller.execute(agent.configuration().id, "/launcher")
            observed.append("commands executed")

        monkeypatch.setattr(AgentChatApp, "run_async", fake_run_async)
        launcher = UserInterfaceLauncher()
        await launcher({agent.configuration().id: agent}, pipeline, UserInterfaceConfiguration(repository).add_command(LauncherCommand()))
        assert observed
        assert repository.close_calls == 0
        await repository.close()
        await agent.close()

        async def default_run_async(_app):
            return None

        monkeypatch.setattr(AgentChatApp, "run_async", default_run_async)
        owned_repository = TrackingRepository()
        monkeypatch.setattr(ui_app, "SqliteSessionRepository", lambda: owned_repository)
        await launcher({agent.configuration().id: agent}, pipeline)
        assert owned_repository.close_calls == 1

        failed_repository = TrackingRepository()
        monkeypatch.setattr(ui_app, "SqliteSessionRepository", lambda: failed_repository)

        async def failing_run_async(_app):
            raise RuntimeError("launch failed")

        monkeypatch.setattr(AgentChatApp, "run_async", failing_run_async)
        with pytest.raises(RuntimeError, match="launch failed"):
            await launcher({agent.configuration().id: agent}, pipeline)
        assert failed_repository.close_calls == 1

    asyncio.run(scenario())
