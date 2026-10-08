

from pairo import StdioMCPServer


def get_github_mcp_server(port = 8085) -> StdioMCPServer: return StdioMCPServer(
            name="github",
            command="docker",
            args=("run","-i","--rm","-p","127.0.0.1:" + str(port) + ":" + str(port),"-e","GITHUB_OAUTH_CALLBACK_PORT","ghcr.io/github/github-mcp-server" ),
            env={"GITHUB_OAUTH_CALLBACK_PORT": str(port)},
            
            )