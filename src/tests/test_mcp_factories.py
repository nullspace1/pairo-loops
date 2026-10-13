"""Contract tests for the packaged MCP server factories."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from pairo.core.agent import Agent, SandboxMode
from pairo.core.mcp.servers import StdioMCPServer
from pairo.mcp.chatroom import Chatroom
from pairo.mcp.context7 import context7
from pairo.mcp.github import add_github_mcp_server


def configured_agent(tmp_path: Path, name: str = "developer") -> Agent:
    return Agent(
        name=name,
        sandbox=SandboxMode.READ_ONLY,
        workspace=tmp_path,
        model="test",
    )


def test_github_factory_configures_agents_with_default_server(tmp_path: Path) -> None:
    agent = configured_agent(tmp_path)
    server = add_github_mcp_server([agent])

    assert server.name == "github"
    assert server.command == "docker"
    assert server.args == (
        "run",
        "-i",
        "--rm",
        "-p",
        "127.0.0.1:8085:8085",
        "-e",
        "GITHUB_OAUTH_CALLBACK_PORT",
        "ghcr.io/github/github-mcp-server",
    )
    assert server.env == {"GITHUB_OAUTH_CALLBACK_PORT": "8085"}
    assert agent.mcp_servers == (server,)


def test_github_factory_applies_a_custom_port_consistently(tmp_path: Path) -> None:
    agent = configured_agent(tmp_path)
    server = add_github_mcp_server([agent], 9123)

    assert "127.0.0.1:9123:9123" in server.args
    assert server.env["GITHUB_OAUTH_CALLBACK_PORT"] == "9123"


def test_context7_factory_configures_agents_and_passes_settings(tmp_path: Path) -> None:
    agents = [
        configured_agent(tmp_path, "developer"),
        configured_agent(tmp_path, "planner"),
    ]
    server = context7(agents, "secret", 2.5)

    assert server.name == "context7"
    assert server.command == "npx"
    assert server.args == ("-y", "@upstash/context7-mcp", "--api-key", "secret")
    assert server.startup_timeout_sec == 2.5
    assert all(agent.mcp_servers == (server,) for agent in agents)


@pytest.mark.parametrize("timeout", [0, -1])
def test_context7_factory_rejects_non_positive_timeouts(timeout) -> None:
    with pytest.raises(ValidationError):
        context7([], "secret", timeout)


def test_chatroom_registers_runner_agent_ids() -> None:
    agent = configured_agent(Path.cwd())
    runner = type("RunnerStub", (), {"agents": {agent.id: agent}})()
    chatroom = Chatroom(port=8081)

    chatroom.add_chatroom("main", runner, [agent])
    assert chatroom._is_in_chatroom("main", agent.id)
    assert not chatroom._is_in_chatroom("main", configured_agent(Path.cwd()).id)
    assert agent.mcp_servers == (chatroom.as_http_mcp_server(),)

    unknown_agent = configured_agent(Path.cwd(), "planner")
    with pytest.raises(ValueError, match="Unknown chatroom agents"):
        chatroom.add_chatroom("invalid", runner, [unknown_agent])
    assert unknown_agent.mcp_servers == ()


def test_repeated_factory_registration_is_idempotent(tmp_path: Path) -> None:
    agent = configured_agent(tmp_path)

    first = add_github_mcp_server([agent])
    second = add_github_mcp_server([agent])

    assert first == second
    assert agent.mcp_servers == (first,)


def test_factory_configures_agents_with_duplicate_names(tmp_path: Path) -> None:
    agents = [
        configured_agent(tmp_path, "shared"),
        configured_agent(tmp_path, "shared"),
    ]

    server = add_github_mcp_server(agents)

    assert agents[0].id != agents[1].id
    assert all(agent.mcp_servers == (server,) for agent in agents)


def test_conflicting_server_does_not_partially_configure_agents(
    tmp_path: Path,
) -> None:
    unconfigured = configured_agent(tmp_path, "developer")
    conflicted = configured_agent(tmp_path, "planner")
    conflicted.add_mcp_server(StdioMCPServer(name="github", command="different"))

    with pytest.raises(ValueError, match="different MCP server"):
        add_github_mcp_server([unconfigured, conflicted])

    assert unconfigured.mcp_servers == ()
