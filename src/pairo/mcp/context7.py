from collections.abc import Sequence

from pairo.core.agent import AgentConfiguration
from pairo.core.mcp.servers import StdioMCPServer

from ._agent_configuration import add_server_to_agents


class Context7Installer:
    def __call__(
        self,
        agents: Sequence[AgentConfiguration],
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


context7 = Context7Installer()

