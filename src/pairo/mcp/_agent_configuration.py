"""Shared registration of MCP server configurations on mutable agents."""

from collections.abc import Sequence
from uuid import UUID

from pairo.core.agent import Agent
from pairo.core.mcp.servers import MCPServer


def add_server_to_agents(
    agents: Sequence[Agent],
    server: MCPServer,
) -> MCPServer:
    """Add one server to each agent without leaving a partially updated list."""
    unique_agents = _unique_agents(agents)
    agents_to_update: list[Agent] = []

    for agent in unique_agents:
        configured_server = next(
            (
                candidate
                for candidate in agent.mcp_servers
                if candidate.name == server.name
            ),
            None,
        )
        if configured_server is None:
            agents_to_update.append(agent)
        elif configured_server != server:
            raise ValueError(
                f"Agent '{agent.name}' already has a different MCP server "
                f"named '{server.name}'."
            )

    for agent in agents_to_update:
        agent.add_mcp_server(server)

    return server


def _unique_agents(agents: Sequence[Agent]) -> tuple[Agent, ...]:
    agents_by_id: dict[UUID, Agent] = {}
    for agent in agents:
        configured_agent = agents_by_id.get(agent.id)
        if configured_agent is not None and configured_agent is not agent:
            raise ValueError(f"Multiple agents use ID '{agent.id}'.")
        agents_by_id[agent.id] = agent
    return tuple(agents_by_id.values())


__all__ = ["add_server_to_agents"]
