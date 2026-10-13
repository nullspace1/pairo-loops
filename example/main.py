"""Run the four example agents on one asyncio event loop."""

import asyncio
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from agents.developer.developer import get_developer
from agents.intern.intern import get_intern
from agents.planner.planner import get_planner
from agents.product_owner.product_owner import get_product_owner
from pairo import CodexAgentClient, Runner, run_agents
from pairo.core.events import EventPipeline
from pairo.mcp.chatroom import Chatroom
from pairo.mcp.github import add_github_mcp_server
from pairo.ui import SqliteSessionRepository


async def run_async() -> None:
    pipeline = EventPipeline()
    repository = SqliteSessionRepository()
    chatroom = Chatroom(port=8081)

    agents = [
        get_product_owner(pipeline, repository),
        get_planner(pipeline, repository),
        get_developer(pipeline, repository),
        get_intern(pipeline, repository),
    ]
    runner = Runner(agents, pipeline, CodexAgentClient())
    chatroom.add_chatroom("main", runner, agents)
    add_github_mcp_server(agents)
    await chatroom.boot_up()
    try:
        await run_agents(runner, repository)
    finally:
        await chatroom.close()


def run() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
