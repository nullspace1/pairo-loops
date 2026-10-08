"""Contract tests for the packaged MCP server factories."""

import pytest
from pydantic import ValidationError

from pairo.mcp.context7 import context7
from pairo.mcp.github import get_github_mcp_server


def test_github_factory_uses_default_loopback_port_and_environment() -> None:
    server = get_github_mcp_server()
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


def test_github_factory_applies_a_custom_port_consistently() -> None:
    server = get_github_mcp_server(9123)
    assert "127.0.0.1:9123:9123" in server.args
    assert server.env["GITHUB_OAUTH_CALLBACK_PORT"] == "9123"


def test_context7_factory_converts_timeout_and_passes_api_key() -> None:
    server = context7("secret", 2500)
    assert server.name == "context7"
    assert server.command == "npx"
    assert server.args == ("-y", "@upstash/context7-mcp", "--api-key", "secret")
    assert server.startup_timeout_sec == 2.5


@pytest.mark.parametrize("timeout", [0, -1])
def test_context7_factory_rejects_non_positive_timeouts(timeout) -> None:
    with pytest.raises(ValidationError):
        context7("secret", timeout)
