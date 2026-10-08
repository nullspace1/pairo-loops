
"""MCP chatroom tools for forwarding messages between registered agents."""

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from pairo.core.agent import Agent
from pairo.core.agent_server import MCP_AGENT_NAME_HEADER
from pairo.core.mcp.mcp_http_wrapper import MCPHttpWrapper


class Chatroom(MCPHttpWrapper):
    """Route chatroom messages to agents while retaining their source identity."""

    def __init__(self, port: int) -> None:
        super().__init__(port=port)
        self.chatrooms: dict[str, list[Agent]] = {}

    def add_chatroom(self, name: str, agents: list[Agent]) -> None:
        self.chatrooms[name] = agents

    def build_mcp_server(self) -> MCPServer:
        mcp = MCPServer(
            "chatroom",
            "Chatroom MCP Server",
            "Send messages between agents registered in a chatroom.",
        )

        def get_agent_name(ctx: Context) -> str | None:
            if ctx.headers is None:
                return None
            return ctx.headers.get(MCP_AGENT_NAME_HEADER)
        
        @mcp.tool("me")
        async def me(ctx: Context) -> str:
            agent_name = get_agent_name(ctx)
            if agent_name is None:
                return "Agent name not configured."
            return agent_name

        @mcp.tool("send_message", "Send a message to an agent in a chatroom.")
        async def send_message(
            chatroom: str,
            destination_agent_name: str,
            message: str,
            ctx: Context,
        ) -> str:
            sender_name = get_agent_name(ctx)
            if sender_name is None:
                return "Agent name not configured."
            if not self._is_in_chatroom(chatroom, sender_name):
                return f"Agent '{sender_name}' is not in chatroom '{chatroom}'."

            destination = self._destination_agent(chatroom, destination_agent_name)
            if destination is None:
                return (
                    f"Agent '{destination_agent_name}' is not registered in "
                    f"chatroom '{chatroom}'."
                )

            forwarded_message = (
                f"[chatroom: {chatroom}] Message from {sender_name}: {message}"
            )
            await destination.send_message(forwarded_message, source=sender_name)
            return (
                f"Message sent to {destination_agent_name} in chatroom "
                f"'{chatroom}'."
            )

        @mcp.tool(
            "get_chatrooms",
            "List chatrooms that contain the calling agent.",
        )
        async def get_chatrooms(ctx: Context) -> list[dict[str, object]] | str:
            agent_name = get_agent_name(ctx)
            if agent_name is None:
                return "Agent name not configured."
            return [
                {
                    "chatroom": name,
                    "agents": [agent.name for agent in agents],
                }
                for name, agents in self.chatrooms.items()
                if any(agent.name == agent_name for agent in agents)
            ]

        return mcp

    def _is_in_chatroom(self, chatroom: str, agent_name: str) -> bool:
        return any(agent.name == agent_name for agent in self.chatrooms.get(chatroom, ()))

    def _destination_agent(
        self,
        chatroom: str,
        destination_agent_name: str,
    ) -> Agent | None:
        return next(
            (
                agent
                for agent in self.chatrooms.get(chatroom, ())
                if agent.name == destination_agent_name
            ),
            None,
        )
