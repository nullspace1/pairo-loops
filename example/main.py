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
from pairo import run_agents
from pairo.core.events import EventPipeline
from pairo.mcp.chatroom import Chatroom
from pairo.mcp.github import get_github_mcp_server
from pairo.ui import SqliteSessionRepository


async def run_async() -> None:
    pipeline = EventPipeline()
    repository = SqliteSessionRepository()
    chatroom = Chatroom(port=8081)
    servers = [chatroom.as_http_mcp_server(), get_github_mcp_server()]

    agents = [
        get_product_owner(pipeline, repository, servers),
        get_planner(pipeline, repository, servers),
        get_developer(pipeline, repository, servers),
        get_intern(pipeline, repository, servers),
    ]
    chatroom.add_chatroom("main", agents)
    await chatroom.boot_up()
    try:
        await run_agents(agents, pipeline, repository)
    finally:
        await chatroom.close()


def run() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
