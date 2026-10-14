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
    CodexAgentClient,
    Runner,
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
    planner = get_planner(pipeline, repository)
    developer = get_developer("developer", pipeline, repository)
    verifier = get_developer("verifier", pipeline, repository)
    agents = [planner, developer, verifier]

    runner = Runner(agents, pipeline, CodexAgentClient())
    spec_manager = install_spec_manager(
        folder=SOURCE_ROOT / "pairo",
        runner=runner,
        director=planner,
        pairs=[WorkerVerifierPair("pair-1", developer, verifier)],
    )
    add_github_mcp_server(agents)
    await spec_manager.boot_up()
    try:
        await run_agents(runner, repository)
    finally:
        await spec_manager.close()


def run() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
