"""Focused contracts for encapsulated runtime and configuration objects."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pairo.core.agent import AgentConfiguration, SandboxPermission
from pairo.core.clients import ActiveSession, AgentStatus, FakeSessionIdentifier
from pairo.core.events import EventPipeline
from pairo.core.mcp.servers import StdioMCPServer
from pairo.mcp._agent_configuration import MCPServerAttacher
from pairo import runtime
from pairo.ui.widgets.sidebar_statistics.default_sidebar_statistics import (
    TimestampFormatter,
    default_sidebar_statistics,
    format_time,
)


class _Agent:
    def __init__(self, calls: list[str], configured: AgentConfiguration) -> None:
        self.calls = calls
        self._configuration = configured

    def configuration(self) -> AgentConfiguration:
        return self._configuration

    def current_session(self):
        return None

    def status(self) -> AgentStatus:
        return AgentStatus.INACTIVE

    async def new_session(self, name: str):
        self.calls.append(f"start:{name}")
        return ActiveSession(
            name=name,
            identifier=FakeSessionIdentifier(session_id="session-1"),
        )

    async def close(self) -> None:
        self.calls.append("close:agent")


class _Repository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def latest_session_for(self, _agent_id):
        return None

    async def save_session(self, _agent_id, name, _identifier) -> None:
        self.calls.append(f"save:{name}")

    async def close(self) -> None:
        self.calls.append("close:repository")


def test_run_agents_uses_ui_launcher(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        pipeline = EventPipeline()
        configured = AgentConfiguration(
            id=uuid4(),
            name="agent",
            model="test",
            sandbox=SandboxPermission.READ_ONLY,
            workspace=Path.cwd(),
        )
        agent = _Agent(calls, configured)
        agents = [agent]
        repository = _Repository(calls)

        async def launch_ui(received_agents, received_pipeline, received_repository, **_kwargs) -> None:
            assert received_agents == {configured.id: agent}
            assert received_pipeline is pipeline
            assert received_repository is repository
            calls.append("ui")

        monkeypatch.setattr(runtime, "run_ui", launch_ui)
        await runtime.run_agents(agents, pipeline, repository, initial_session_name="Initial")

        assert calls == [
            "start:Initial",
            "save:Initial",
            "ui",
            "close:agent",
            "close:repository",
        ]
        assert not hasattr(pipeline, "_closed")

    asyncio.run(scenario())


def test_mcp_server_attacher_attaches_its_injected_server() -> None:
    agent = AgentConfiguration(
        id=uuid4(),
        name="developer",
        model="test",
        sandbox=SandboxPermission.READ_ONLY,
        workspace=Path.cwd(),
    )
    server = StdioMCPServer(name="example", command="example")

    attached = MCPServerAttacher(server).attach((agent,))

    assert attached is server
    assert agent.mcp_servers == (server,)


def test_default_statistics_are_shared_and_formatters_keep_behavior() -> None:
    timestamp = datetime(2026, 10, 15, 12, 30, 45, tzinfo=UTC)

    assert [statistic.key for statistic in default_sidebar_statistics][-1] == "token_usage"
    assert [statistic.key for statistic in default_sidebar_statistics if statistic.category.value == "session"] == [
        "session_name", "created", "last_message", "last_update", "token_usage",
    ]
    local_timestamp = timestamp.astimezone()
    offset = local_timestamp.strftime("%z")
    expected_timestamp = f"{local_timestamp:%Y-%m-%d %H:%M:%S} {offset[:3]}:{offset[3:]}"
    assert TimestampFormatter().format(timestamp) == expected_timestamp
    assert format_time(timestamp) == TimestampFormatter().format(timestamp)
