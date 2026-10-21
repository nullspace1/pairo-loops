"""Run one planner with a developer/verifier pair."""

import asyncio
from pathlib import Path
import sys

from pairo.tools.conversation_event_log import ConversationEventLog

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from agents.agent_documenter.agent_documenter import get_agent_documenter
from agents.developer.developer import get_developer
from agents.planner.planner import get_planner
from agents.verifier.verifier import get_verifier
from pairo import (
    WorkerVerifierPair,
    install_spec_manager,
    run_agents,
)
from pairo.core.events import EventPipeline
from pairo.mcp.github import add_github_mcp_server
from pairo.ui import SqliteSessionRepository


async def run_async() -> None:
    pipeline = EventPipeline()
    repository = SqliteSessionRepository()
    event_log = ConversationEventLog(pipeline, Path("./log/events.jsonl"))
    configured_agents = [
        get_planner(pipeline, repository),
        get_developer("developer", pipeline, repository),
        get_verifier(pipeline, repository),
        get_agent_documenter(pipeline, repository),
    ]
    planner, developer, verifier, _agent_documenter = configured_agents
    add_github_mcp_server(
        [
            agent.configuration()
            for agent in (planner, developer, verifier)
        ]
    )
    agents = {
        agent.configuration().id: agent for agent in configured_agents
    }
    spec_manager = install_spec_manager(
        folder=Path("./tmp"),
        agents=agents,
        director=planner,
        pairs=[WorkerVerifierPair("pair-1", developer, verifier)],
        documenter=_agent_documenter,
    )
    try:
        await spec_manager.boot_up()
        await run_agents(agents, pipeline, repository)
    finally:
        await spec_manager.close()
        await event_log.close()


def run() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
