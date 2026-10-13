from collections.abc import Sequence

from pairo.core.agent import Agent
from pairo.core.mcp.servers import StdioMCPServer

from ._agent_configuration import add_server_to_agents


def context7(
    agents: Sequence[Agent],
    api_key: str,
    startup_timeout_sec: float,
) -> StdioMCPServer:
    server = StdioMCPServer(
        name="context7",
        command="npx",
        args=("-y", "@upstash/context7-mcp", "--api-key", api_key),
        startup_timeout_sec=startup_timeout_sec,
    )
    return add_server_to_agents(agents, server)
