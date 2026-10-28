"""Tests for the high-level asynchronous agent application lifecycle."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from pairo import run_agents
from pairo import runtime
from pairo.core.agent import AgentConfiguration, SandboxPermission
from pairo.core.clients import ActiveSession, AgentStatus, FakeAgent, FakeSessionIdentifier
from pairo.core.events import EventPipeline
from pairo.ui import SlashCommand, app as ui_app
from pairo.ui.app import AgentChatApp, run_ui
from pairo.ui.models import SessionMetadata
from pairo.ui.transcribers import default_transcribers
from pairo.ui.widgets.sidebar_statistics import create_default_sidebar_statistics, default_sidebar_statistics
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

    async def activate_session(self, name: str, identifier):
        self.calls.append(f"activate:{name}:{identifier['session_id']}")
        return ActiveSession(
            name=name,
            identifier=FakeSessionIdentifier.model_validate(identifier),
        )

    async def close(self) -> None:
        self.calls.append("close:agent")


class StubRepository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.latest_sessions: dict[UUID, SessionMetadata] = {}

    async def latest_session_for(self, agent_id):
        return self.latest_sessions.get(agent_id)

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

    async def __call__(self, agents, pipeline, repository, **_kwargs) -> None:
        app = AgentChatApp(
            agents, pipeline, repository,
            transcribers=default_transcribers(),
            sidebar_statistics=create_default_sidebar_statistics(),
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
        agents = [agent]
        repository = StubRepository(calls)

        async def fake_run_ui(received_agents, received_pipeline, received_repository, **_kwargs):
            assert received_agents == {agent.configuration().id: agent}
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


def test_run_agents_forwards_ui_options_and_closes_default_repository(monkeypatch, tmp_path: Path) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        agent = StubAgent(calls, tmp_path)
        repository = StubRepository(calls)
        transcribers = default_transcribers()
        statistics = list(create_default_sidebar_statistics())
        monkeypatch.setattr(runtime, "SqliteSessionRepository", lambda: repository)

        async def fake_ui(agents, pipeline, received_repository, *, transcribers, sidebar_statistics):
            assert received_repository is repository
            assert transcribers is registry
            assert sidebar_statistics is statistics
            calls.append("ui")

        registry = transcribers
        monkeypatch.setattr(runtime, "run_ui", fake_ui)
        await run_agents([agent], EventPipeline(), transcribers=registry, sidebar_statistics=statistics)
        assert calls[-2:] == ["close:agent", "close:repository"]

    asyncio.run(scenario())


def test_run_agents_rejects_duplicate_agent_ids(tmp_path: Path) -> None:
    async def scenario() -> None:
        agent = StubAgent([], tmp_path)
        with pytest.raises(ValueError, match="Agent IDs must be unique"):
            await run_agents([agent, agent], EventPipeline(), StubRepository([]))

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
            run_agents([agent], pipeline, repository),
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

        async def fake_run_ui(*_args, **_kwargs):
            if ui_error is not None:
                raise ui_error
            raise AssertionError("UI should not run after startup failure")

        monkeypatch.setattr(runtime, "run_ui", fake_run_ui)
        with pytest.raises(RuntimeError, match=expected):
            await run_agents(
                [agent],
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
        launched_statistics: list[list[object]] = []

        async def fake_run_async(app):
            launched_statistics.append(app._sidebar_statistics)
            await app._controller.execute(agent.configuration().id, "/launcher")
            observed.append("commands executed")

        monkeypatch.setattr(AgentChatApp, "run_async", fake_run_async)
        launcher = run_ui
        await launcher({agent.configuration().id: agent}, pipeline, repository, commands=[LauncherCommand()])
        assert observed
        assert [statistic.key for statistic in launched_statistics[0]][:2] == [
            "name", "id"
        ]
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


def test_run_agents_resumes_latest_session_without_overwriting_identifier(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        pipeline = EventPipeline()
        agent = StubAgent(calls, tmp_path)
        agent_id = agent.configuration().id
        repository = StubRepository(calls)
        identifier = {"provider": "fake", "session_id": "persisted-session"}
        repository.latest_sessions[agent_id] = SessionMetadata(
            agent_id=agent_id,
            session_name="Most Recent",
            identifier=identifier,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_message_at=None,
            last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        async def fake_run_ui(*_args, **_kwargs) -> None:
            calls.append("ui")

        monkeypatch.setattr(runtime, "run_ui", fake_run_ui)
        await run_agents(
            [agent],
            pipeline,
            repository,
            initial_session_name="Fallback",
        )

        assert "activate:Most Recent:persisted-session" in calls
        assert not any(call.startswith("start:") or call.startswith("save:") for call in calls)
        assert calls[-2:] == ["close:agent", "close:repository"]

    asyncio.run(scenario())


def test_run_agents_resumes_and_creates_sessions_independently(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        pipeline = EventPipeline()
        resumed = StubAgent(calls, tmp_path)
        created = StubAgent(calls, tmp_path)
        repository = StubRepository(calls)
        resumed_id = resumed.configuration().id
        repository.latest_sessions[resumed_id] = SessionMetadata(
            agent_id=resumed_id,
            session_name="Existing",
            identifier={"provider": "fake", "session_id": "existing-id"},
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_message_at=None,
            last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        async def fake_run_ui(*_args, **_kwargs) -> None:
            calls.append("ui")

        monkeypatch.setattr(runtime, "run_ui", fake_run_ui)
        await run_agents(
            [resumed, created],
            pipeline,
            repository,
            initial_session_name="Fallback",
        )

        assert "activate:Existing:existing-id" in calls
        assert calls.count("start:Fallback") == 1
        assert calls.count("save:Fallback") == 1
        assert calls.count("close:agent") == 2
        assert calls[-1] == "close:repository"

    asyncio.run(scenario())


def test_run_agents_does_not_persist_fallbacks_after_partial_startup_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        pipeline = EventPipeline()
        both_started = asyncio.Event()
        started = 0

        class ConcurrentAgent(StubAgent):
            async def new_session(self, name: str):
                nonlocal started
                started += 1
                if started == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), timeout=1)
                return await super().new_session(name)

        successful = ConcurrentAgent(calls, tmp_path)
        failing = ConcurrentAgent(calls, tmp_path, start_error=RuntimeError("startup failed"))
        repository = StubRepository(calls)

        async def fake_run_ui(*_args, **_kwargs) -> None:
            raise AssertionError("UI should not run after startup failure")

        monkeypatch.setattr(runtime, "run_ui", fake_run_ui)
        with pytest.raises(RuntimeError, match="startup failed"):
            await run_agents(
                [successful, failing],
                pipeline,
                repository,
                initial_session_name="Fallback",
            )

        assert calls.count("start:Fallback") == 2
        assert not any(call.startswith("save:") for call in calls)
        assert calls.count("close:agent") == 2
        assert calls[-1] == "close:repository"

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_stage", ["lookup", "activation"])
@pytest.mark.parametrize("creation_fails", [False, True])
def test_run_agents_falls_back_once_after_resume_failure(
    monkeypatch, tmp_path: Path, failure_stage: str, creation_fails: bool
) -> None:
    async def scenario() -> None:
        calls: list[str] = []

        class FailingRepository(StubRepository):
            async def latest_session_for(self, agent_id):
                if failure_stage == "lookup":
                    raise RuntimeError("lookup failed")
                return await super().latest_session_for(agent_id)

        class FailingActivationAgent(StubAgent):
            async def activate_session(self, name, identifier):
                calls.append(f"activate:{name}")
                raise RuntimeError("activation failed")

        agent = FailingActivationAgent(
            calls, tmp_path,
            start_error=RuntimeError("creation failed") if creation_fails else None,
        )
        agent_id = agent.configuration().id
        repository = FailingRepository(calls)
        repository.latest_sessions[agent_id] = SessionMetadata(
            agent_id=agent_id, session_name="Existing",
            identifier={"provider": "fake", "session_id": "existing-id"},
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_message_at=None,
            last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        async def fake_run_ui(*_args, **_kwargs) -> None:
            calls.append("ui")

        monkeypatch.setattr(runtime, "run_ui", fake_run_ui)
        if creation_fails:
            with pytest.raises(RuntimeError, match="creation failed"):
                await run_agents([agent], EventPipeline(), repository)
        else:
            await run_agents([agent], EventPipeline(), repository)

        assert calls.count("start:Conversation 1") == 1
        assert ("activate:Existing" in calls) == (failure_stage == "activation")
        assert ("save:Conversation 1" in calls) == (not creation_fails)
        assert ("ui" in calls) == (not creation_fails)
        assert calls[-2:] == ["close:agent", "close:repository"]

    asyncio.run(scenario())
