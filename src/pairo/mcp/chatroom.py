"""MCP chatroom tools for forwarding messages between registered agents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from pairo.core.agent import JsonObject, SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.clients.client import MCP_AGENT_ID_HEADER
from pairo.core.mcp.mcp_http_wrapper import MCPHttpWrapper

from ._agent_configuration import add_server_to_agents


@dataclass(frozen=True, slots=True)
class _ChatroomRegistration:
    agents: Mapping[UUID, Agent[SessionIdentifier]]


class Chatroom(MCPHttpWrapper):
    """Route chatroom messages to agents while retaining their source identity."""

    def __init__(self, port: int) -> None:
        super().__init__(port=port)
        self.chatrooms: dict[str, _ChatroomRegistration] = {}

    def add_chatroom(
        self,
        name: str,
        agents: Mapping[UUID, Agent[SessionIdentifier]],
    ) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("Chatroom name cannot be empty.")
        configured = dict(agents)
        if not configured:
            raise ValueError("A chatroom requires at least one agent.")
        for agent_id, agent in configured.items():
            if agent.configuration().id != agent_id:
                raise ValueError(
                    f"Chatroom key '{agent_id}' does not match its agent ID."
                )
        add_server_to_agents(
            [agent.configuration() for agent in configured.values()],
            self.as_http_mcp_server(),
        )
        self.chatrooms[normalized] = _ChatroomRegistration(
            MappingProxyType(configured)
        )

    def build_mcp_server(self) -> MCPServer:
        mcp = MCPServer(
            "chatroom",
            "Chatroom MCP Server",
            "Send messages between agents registered in a chatroom.",
        )

        @mcp.tool("me")
        async def me(ctx: Context) -> str:
            agent_id = self._agent_id(ctx)
            return str(agent_id) if agent_id is not None else "Agent ID not configured."

        @mcp.tool("send_message", "Send a message to an agent in a chatroom.")
        async def send_message(
            chatroom: str,
            destination_agent_id: str,
            message: str,
            ctx: Context,
        ) -> str:
            sender_id = self._agent_id(ctx)
            if sender_id is None:
                return "Agent ID not configured."
            registration = self.chatrooms.get(chatroom)
            if registration is None or sender_id not in registration.agents:
                return f"Agent '{sender_id}' is not in chatroom '{chatroom}'."
            try:
                destination_id = UUID(destination_agent_id)
            except ValueError:
                return f"Agent ID '{destination_agent_id}' is invalid."
            destination = registration.agents.get(destination_id)
            if destination is None:
                return (
                    f"Agent '{destination_agent_id}' is not registered in "
                    f"chatroom '{chatroom}'."
                )
            sender_name = registration.agents[sender_id].configuration().name
            destination_name = destination.configuration().name
            await destination.send_message(
                f"[chatroom: {chatroom}] Message from {sender_name}: {message}",
                source=sender_name,
            )
            return (
                f"Message sent to {destination_name} ({destination_id}) in chatroom "
                f"'{chatroom}'."
            )

        @mcp.tool(
            "create_session",
            "Create and select a new session for an agent in a chatroom.",
        )
        async def create_session(
            chatroom: str,
            destination_agent_id: str,
            session_name: str,
            ctx: Context,
        ) -> str:
            sender_id = self._agent_id(ctx)
            if sender_id is None:
                return "Agent ID not configured."
            registration = self.chatrooms.get(chatroom)
            if registration is None or sender_id not in registration.agents:
                return f"Agent '{sender_id}' is not in chatroom '{chatroom}'."
            try:
                destination_id = UUID(destination_agent_id)
            except ValueError:
                return f"Agent ID '{destination_agent_id}' is invalid."
            destination = registration.agents.get(destination_id)
            if destination is None:
                return (
                    f"Agent '{destination_agent_id}' is not registered in "
                    f"chatroom '{chatroom}'."
                )
            created = await destination.new_session(session_name)
            destination_name = destination.configuration().name
            return (
                f"Created session '{created.name}' for {destination_name} "
                f"({destination_id}) in chatroom '{chatroom}'."
            )

        @mcp.tool("get_chatrooms", "List chatrooms that contain the calling agent.")
        async def get_chatrooms(ctx: Context) -> list[JsonObject] | str:
            agent_id = self._agent_id(ctx)
            if agent_id is None:
                return "Agent ID not configured."
            return [
                {
                    "chatroom": name,
                    "agents": [
                        {
                            "id": str(configured_id),
                            "name": agent.configuration().name,
                        }
                        for configured_id, agent in registration.agents.items()
                    ],
                }
                for name, registration in self.chatrooms.items()
                if agent_id in registration.agents
            ]

        return mcp

    def _is_in_chatroom(self, chatroom: str, agent_id: UUID) -> bool:
        registration = self.chatrooms.get(chatroom)
        return registration is not None and agent_id in registration.agents

    def _agent_id(self, ctx: Context) -> UUID | None:
        value = ctx.headers.get(MCP_AGENT_ID_HEADER) if ctx.headers else None
        try:
            return UUID(value) if value is not None else None
        except ValueError:
            return None
