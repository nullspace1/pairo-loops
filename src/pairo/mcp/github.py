from collections.abc import Sequence

from pairo.core.agent import Agent
from pairo.core.mcp.servers import StdioMCPServer

from ._agent_configuration import add_server_to_agents


def add_github_mcp_server(
    agents: Sequence[Agent],
    port: int = 8085,
) -> StdioMCPServer:
    port_text = str(port)
    server = StdioMCPServer(
        name="github",
        command="docker",
        args=(
            "run",
            "-i",
            "--rm",
            "-p",
            f"127.0.0.1:{port_text}:{port_text}",
            "-e",
            "GITHUB_OAUTH_CALLBACK_PORT",
            "ghcr.io/github/github-mcp-server",
        ),
        env={"GITHUB_OAUTH_CALLBACK_PORT": port_text},
    )
    return add_server_to_agents(agents, server)
