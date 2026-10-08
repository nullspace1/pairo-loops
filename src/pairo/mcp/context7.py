
from pairo import StdioMCPServer


def context7(api_key : str, startup_timeout_ms : float) -> StdioMCPServer:
    return StdioMCPServer(
        name="context7",
        command="npx",
        args=("-y", "@upstash/context7-mcp", "--api-key", api_key),
        startup_timeout_sec= startup_timeout_ms / 1000
    )