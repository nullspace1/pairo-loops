"""Run the coordinated agents and one standalone development agent."""

import asyncio
import datetime
from pathlib import Path
import sys

from pairo.core.agent.session_identifier import SessionIdentifier
from pairo.tools.conversation_event_log import (
    ConversationEventLog,
    DiagnosticEventLogPolicy,
)

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from agents.agent_documenter.agent_documenter import get_agent_documenter
from agents.developer.developer import get_developer
from agents.isolated_developer.isolated_developer import get_isolated_developer
from agents.planner.planner import get_planner
from agents.prompt_optimizer.prompt_optimizer import get_prompt_optimizer
from agents.verifier.verifier import get_verifier
from pairo import Agent, run_agents
from pairo.core.events import EventPipeline
from pairo.mcp.github import add_github_mcp_server
from pairo.ui import SqliteSessionRepository
from spec_manager import WorkerVerifierPair, install_spec_manager


async def run_async() -> None:
    pipeline = EventPipeline()
    repository = SqliteSessionRepository("./.db/sessions.db")
    event_log = ConversationEventLog(
        pipeline,
        Path("./.log/events" + str(datetime.datetime.now().timestamp()) + ".jsonl"),
        DiagnosticEventLogPolicy(),
    )
    configured_agents : list[Agent[SessionIdentifier]] = [
        get_planner(pipeline, repository),
        get_developer("developer", pipeline, repository),
        get_verifier(pipeline, repository),
        get_agent_documenter(pipeline, repository),
        get_prompt_optimizer(pipeline, repository),
        get_isolated_developer(pipeline, repository),
    ]
    (
        planner,
        developer,
        verifier,
        _agent_documenter,
        _prompt_optimizer,
        _isolated_developer,
    ) = configured_agents
    add_github_mcp_server(
        [
            agent.configuration()
            for agent in (planner, developer, verifier)
        ]
    )
    spec_manager = install_spec_manager(
        folder=Path("./tmp"),
        agents=configured_agents,
        director=planner,
        pairs=[WorkerVerifierPair("pair-1", developer, verifier)],
        documenter=_agent_documenter,
    )
    try:
        await spec_manager.boot_up()
        await run_agents(
            configured_agents,
            pipeline,
            repository,
            initial_session_name="Agentic Loops",
        )
    finally:
        await event_log.close()
        await spec_manager.close()


def run() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
