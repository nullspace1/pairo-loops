"""Focused contracts for encapsulated runtime and configuration objects."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pairo.core.agent import Agent, SandboxMode
from pairo.core.events import EventPipeline
from pairo.core.mcp.servers import StdioMCPServer
from pairo.mcp._agent_configuration import MCPServerAttacher
from pairo.runtime import RuntimeLauncher
from pairo.ui.widgets.sidebar_statistics.default_sidebar_statistics import (
    DefaultSidebarStatisticsFactory,
    DurationFormatter,
    TimestampFormatter,
    default_sidebar_statistics,
    format_duration,
    format_time,
)


class _Runner:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.pipeline = EventPipeline()

    async def start(self, name: str) -> None:
        self.calls.append(f"start:{name}")

    async def close(self) -> None:
        self.calls.append("close:runner")


class _Repository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def close(self) -> None:
        self.calls.append("close:repository")


def test_runtime_launcher_uses_its_injected_ui_launcher() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        runner = _Runner(calls)
        repository = _Repository(calls)

        async def launch_ui(received_runner, received_repository) -> None:
            assert received_runner is runner
            assert received_repository is repository
            calls.append("ui")

        await RuntimeLauncher(runner, repository, launch_ui).launch("Initial")

        assert calls == ["start:Initial", "ui", "close:runner", "close:repository"]
        assert runner.pipeline._closed

    asyncio.run(scenario())


def test_mcp_server_attacher_attaches_its_injected_server() -> None:
    agent = Agent(
        name="developer",
        model="test",
        sandbox=SandboxMode.READ_ONLY,
        workspace=Path.cwd(),
    )
    server = StdioMCPServer(name="example", command="example")

    attached = MCPServerAttacher(server).attach((agent,))

    assert attached is server
    assert agent.mcp_servers == (server,)


def test_default_statistics_factory_and_formatters_keep_legacy_behavior() -> None:
    agent = Agent(
        name="developer",
        model="test",
        sandbox=SandboxMode.READ_ONLY,
        workspace=Path.cwd(),
    )
    timestamp = datetime(2026, 10, 15, 12, 30, 45, tzinfo=UTC)
    statistics = DefaultSidebarStatisticsFactory(agent).build()

    assert [statistic.key for statistic in statistics.agent_information][-1] == (
        "mcp_servers"
    )
    assert [statistic.key for statistic in statistics.current_session] == [
        "session_name",
        "created",
        "last_message",
        "last_update",
        "duration",
        "token_usage",
    ]
    local_timestamp = timestamp.astimezone()
    offset = local_timestamp.strftime("%z")
    expected_timestamp = (
        f"{local_timestamp:%Y-%m-%d %H:%M:%S} {offset[:3]}:{offset[3:]}"
    )
    assert TimestampFormatter().format(timestamp) == expected_timestamp
    assert DurationFormatter().format(-3.2) == "00:00:00"
    assert format_time(timestamp) == TimestampFormatter().format(timestamp)
    assert format_duration(3661.9) == DurationFormatter().format(3661.9)
    assert [
        statistic.key
        for statistic in default_sidebar_statistics(agent).agent_information
    ] == [statistic.key for statistic in statistics.agent_information]
