"""Run one planner with a developer/verifier pair."""

import asyncio
import datetime
from pathlib import Path
import sys

from pairo.tools.conversation_event_log import (
    ConversationEventLog,
    DiagnosticEventLogPolicy,
)

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from agents.agent_documenter.agent_documenter import get_agent_documenter
from agents.developer.developer import get_developer
from agents.planner.planner import get_planner
from agents.prompt_optimizer.prompt_optimizer import get_prompt_optimizer
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
    repository = SqliteSessionRepository("./.log/sessions.db")
    event_log = ConversationEventLog(
        pipeline,
        Path("./.log/events" + str(datetime.datetime.now().timestamp()) + ".jsonl"),
        DiagnosticEventLogPolicy(),
    )
    configured_agents = [
        get_planner(pipeline, repository),
        get_developer("developer", pipeline, repository),
        get_verifier(pipeline, repository),
        get_agent_documenter(pipeline, repository),
        get_prompt_optimizer(pipeline, repository),
    ]
    planner, developer, verifier, _agent_documenter, _prompt_optimizer = (
        configured_agents
    )
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
        await event_log.close()
        await spec_manager.close()

        
        


def run() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
