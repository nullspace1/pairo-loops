"""Run one planner with a developer/verifier pair."""

import asyncio
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from agents.developer.developer import get_developer
from agents.planner.planner import get_planner
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
    configured_agents = [
        get_planner(pipeline, repository),
        get_developer("developer", pipeline, repository),
        get_developer("verifier", pipeline, repository),
    ]
    add_github_mcp_server(
        [agent.configuration() for agent in configured_agents]
    )
    agents = {
        agent.configuration().id: agent for agent in configured_agents
    }
    planner, developer, verifier = configured_agents
    spec_manager = install_spec_manager(
        folder=SOURCE_ROOT / "pairo",
        agents=agents,
        director=planner,
        pairs=[WorkerVerifierPair("pair-1", developer, verifier)],
    )
    await spec_manager.boot_up()
    try:
        await run_agents(agents, pipeline, repository)
    finally:
        await spec_manager.close()


def run() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
