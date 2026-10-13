"""MCP chatroom tools for forwarding messages between registered agents."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from pairo.core.agent import Agent, Runner
from pairo.core.clients.client import MCP_AGENT_ID_HEADER
from pairo.core.mcp.mcp_http_wrapper import MCPHttpWrapper

from ._agent_configuration import add_server_to_agents


@dataclass(frozen=True, slots=True)
class _ChatroomRegistration:
    runner: Runner
    agent_ids: tuple[UUID, ...]


class Chatroom(MCPHttpWrapper):
    """Route chatroom messages to agents while retaining their source identity."""

    def __init__(self, port: int) -> None:
        super().__init__(port=port)
        self.chatrooms: dict[str, _ChatroomRegistration] = {}

    def add_chatroom(
        self,
        name: str,
        runner: Runner,
        agents: Sequence[Agent],
    ) -> None:
        unknown = [
            str(agent.id)
            for agent in agents
            if runner.agents.get(agent.id) is not agent
        ]
        if unknown:
            raise ValueError(f"Unknown chatroom agents: {', '.join(unknown)}")
        add_server_to_agents(agents, self.as_http_mcp_server())
        self.chatrooms[name] = _ChatroomRegistration(
            runner,
            tuple(agent.id for agent in agents),
        )

    def build_mcp_server(self) -> MCPServer:
        mcp = MCPServer(
            "chatroom",
            "Chatroom MCP Server",
            "Send messages between agents registered in a chatroom.",
        )

        def get_agent_id(ctx: Context) -> UUID | None:
            if ctx.headers is None:
                return None
            value = ctx.headers.get(MCP_AGENT_ID_HEADER)
            try:
                return UUID(value) if value is not None else None
            except ValueError:
                return None

        @mcp.tool("me")
        async def me(ctx: Context) -> str:
            agent_id = get_agent_id(ctx)
            if agent_id is None:
                return "Agent ID not configured."
            return str(agent_id)

        @mcp.tool("send_message", "Send a message to an agent in a chatroom.")
        async def send_message(
            chatroom: str,
            destination_agent_id: str,
            message: str,
            ctx: Context,
        ) -> str:
            sender_id = get_agent_id(ctx)
            if sender_id is None:
                return "Agent ID not configured."
            if not self._is_in_chatroom(chatroom, sender_id):
                return f"Agent '{sender_id}' is not in chatroom '{chatroom}'."

            try:
                destination_id = UUID(destination_agent_id)
            except ValueError:
                return f"Agent ID '{destination_agent_id}' is invalid."

            registration = self.chatrooms.get(chatroom)
            if (
                registration is None
                or destination_id not in registration.agent_ids
            ):
                return (
                    f"Agent '{destination_agent_id}' is not registered in "
                    f"chatroom '{chatroom}'."
                )

            sender_name = registration.runner.agents[sender_id].name
            destination_name = registration.runner.agents[destination_id].name
            forwarded_message = (
                f"[chatroom: {chatroom}] Message from {sender_name}: {message}"
            )
            await registration.runner.send_message(
                destination_id,
                forwarded_message,
                source=sender_name,
            )
            return (
                f"Message sent to {destination_name} ({destination_id}) in chatroom "
                f"'{chatroom}'."
            )

        @mcp.tool(
            "get_chatrooms",
            "List chatrooms that contain the calling agent.",
        )
        async def get_chatrooms(ctx: Context) -> list[dict[str, object]] | str:
            agent_id = get_agent_id(ctx)
            if agent_id is None:
                return "Agent ID not configured."
            return [
                {
                    "chatroom": name,
                    "agents": [
                        {
                            "id": str(configured_id),
                            "name": registration.runner.agents[configured_id].name,
                        }
                        for configured_id in registration.agent_ids
                    ],
                }
                for name, registration in self.chatrooms.items()
                if agent_id in registration.agent_ids
            ]

        return mcp

    def _is_in_chatroom(self, chatroom: str, agent_id: UUID) -> bool:
        registration = self.chatrooms.get(chatroom)
        return registration is not None and agent_id in registration.agent_ids
