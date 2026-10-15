"""Shared MCP server attachment for mutable agents."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Generic, TypeVar
from uuid import UUID

from pairo.core.agent import Agent
from pairo.core.mcp.servers import MCPServer


MCPServerT = TypeVar("MCPServerT", bound=MCPServer)


class MCPServerAttacher(Generic[MCPServerT]):
    """Attach one configured server to agents as one atomic operation."""

    def __init__(self, server: MCPServerT) -> None:
        self._server = server

    def attach(self, agents: Sequence[Agent]) -> MCPServerT:
        """Validate every agent before adding the server to any of them."""
        unique_agents = self._unique_agents(agents)
        agents_to_update = self._agents_requiring_server(unique_agents)
        for agent in agents_to_update:
            agent.add_mcp_server(self._server)
        return self._server

    def _unique_agents(self, agents: Sequence[Agent]) -> tuple[Agent, ...]:
        agents_by_id: dict[UUID, Agent] = {}
        for agent in agents:
            configured_agent = agents_by_id.get(agent.id)
            if configured_agent is not None and configured_agent is not agent:
                raise ValueError(f"Multiple agents use ID '{agent.id}'.")
            agents_by_id[agent.id] = agent
        return tuple(agents_by_id.values())

    def _agents_requiring_server(self, agents: Sequence[Agent]) -> tuple[Agent, ...]:
        agents_to_update: list[Agent] = []
        for agent in agents:
            configured_server = next(
                (
                    candidate
                    for candidate in agent.mcp_servers
                    if candidate.name == self._server.name
                ),
                None,
            )
            if configured_server is None:
                agents_to_update.append(agent)
            elif configured_server != self._server:
                raise ValueError(
                    f"Agent '{agent.name}' already has a different MCP server "
                    f"named '{self._server.name}'."
                )
        return tuple(agents_to_update)


def add_server_to_agents(
    agents: Sequence[Agent],
    server: MCPServerT,
) -> MCPServerT:
    """Compatibility entry point for attaching a server to agents."""
    return MCPServerAttacher(server).attach(agents)


__all__ = ["MCPServerAttacher", "add_server_to_agents"]
