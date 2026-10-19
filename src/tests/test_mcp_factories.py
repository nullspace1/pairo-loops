"""Contract tests for the packaged MCP server factories."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pairo.core.agent import AgentConfiguration, SandboxMode
from pairo.core.mcp.servers import StdioMCPServer
from pairo.mcp.chatroom import Chatroom
from pairo.mcp.context7 import context7
from pairo.mcp.github import add_github_mcp_server


def configured_agent(tmp_path: Path, name: str = "developer") -> AgentConfiguration:
    return AgentConfiguration(
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


class ChatroomAgentStub:
    def __init__(self, configured: AgentConfiguration) -> None:
        self._configuration = configured
        self.created: list[str] = []
        self.messages: list[tuple[str, str]] = []

    def configuration(self) -> AgentConfiguration:
        return self._configuration

    async def new_session(self, name: str):
        self.created.append(name)
        return SimpleNamespace(name=name)

    async def send_message(self, message: str, source: str = "user") -> None:
        self.messages.append((message, source))


def test_chatroom_registers_agent_ids() -> None:
    configured = configured_agent(Path.cwd())
    agent = ChatroomAgentStub(configured)
    chatroom = Chatroom(port=8081)

    chatroom.add_chatroom("main", {configured.id: agent})
    assert chatroom._is_in_chatroom("main", configured.id)
    assert not chatroom._is_in_chatroom("main", configured_agent(Path.cwd()).id)
    assert configured.mcp_servers == (chatroom.as_http_mcp_server(),)

    with pytest.raises(ValueError, match="does not match"):
        chatroom.add_chatroom("invalid", {configured_agent(Path.cwd()).id: agent})


def test_chatroom_can_create_a_session_for_a_registered_agent() -> None:
    async def scenario() -> None:
        sender_config = configured_agent(Path.cwd(), "sender")
        recipient_config = configured_agent(Path.cwd(), "recipient")
        sender = ChatroomAgentStub(sender_config)
        recipient = ChatroomAgentStub(recipient_config)
        chatroom = Chatroom(port=8081)
        chatroom.add_chatroom(
            "main",
            {sender_config.id: sender, recipient_config.id: recipient},
        )
        server = chatroom.build_mcp_server()
        tool = server._tool_manager.get_tool("create_session")

        result = await tool.fn(
            "main",
            str(recipient_config.id),
            "Review branch",
            SimpleNamespace(headers={"X-Agent-ID": str(sender_config.id)}),
        )

        assert recipient.created == ["Review branch"]
        assert "Created session 'Review branch'" in result

    asyncio.run(scenario())


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
