"""Run planner and developer agents on one asyncio event loop."""

import asyncio
from pathlib import Path
import sys

from pairo.mcp.github import get_github_mcp_server

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pairo import  run_agents
from pairo.core import (
    Agent,
    AgentConfiguration,
    ApprovalMode,
    CodexAgentClient,
    ReasoningEffort,
    Sandbox,
    WebSearchMode,
)
from pairo.core.events import EventPipeline
from pairo.core.mcp.mcp_http_wrapper import MCPHttpWrapper
from pairo.ui import SqliteSessionRepository, TextualPermissionRequester
from pairo.core.mcp.servers import MCPServer
from pairo.mcp.chatroom import Chatroom



async def run_async() -> None:
    pipeline = EventPipeline()
    repository = SqliteSessionRepository()
    
    chatroom = Chatroom(port=8081)
    servers : list[MCPServer] = [
        chatroom.as_http_mcp_server(),
        get_github_mcp_server()
        ]
        
    planner = Agent(
        name="planner",
        event_pipeline=pipeline,
        client=CodexAgentClient(),
        config=AgentConfiguration(
            approval_mode=ApprovalMode.USER,
            permission_requester=TextualPermissionRequester(pipeline, repository),
            model="gpt-5.6-sol",
            workspace=SOURCE_ROOT,
            reasoning_effort=ReasoningEffort.HIGH,
            sandbox=Sandbox.READ_ONLY,
            web_search=WebSearchMode.CACHED,
            mcp_servers=servers
        ),
    )
    
    
    developer = Agent (
        name="developer",
        event_pipeline=pipeline,
        client=CodexAgentClient(),
        config=AgentConfiguration(
            sandbox=Sandbox.WORKSPACE_WRITE,
            approval_mode=ApprovalMode.USER,
            permission_requester=TextualPermissionRequester(pipeline, repository),
            workspace=SOURCE_ROOT,
            model="gpt-5.6-terra",
            reasoning_effort=ReasoningEffort.LOW,
            web_search=WebSearchMode.DISABLED,
            mcp_servers=servers,
        ),
    )
    
    documenter = Agent (
        name="documenter",
        event_pipeline=pipeline,
        client=CodexAgentClient(),
        config=AgentConfiguration(
            sandbox=Sandbox.WORKSPACE_WRITE,
            approval_mode=ApprovalMode.USER,
            permission_requester=TextualPermissionRequester(pipeline, repository),
            workspace=SOURCE_ROOT,
            model="gpt-5.6-terra",
            reasoning_effort=ReasoningEffort.LOW,
            web_search=WebSearchMode.DISABLED,
            mcp_servers=servers,
        )
    )
    
    chatroom.add_chatroom("main", [planner, developer, documenter])
    await chatroom.boot_up()
    
    await run_agents((planner, developer, documenter), pipeline, repository)
    await chatroom.close()


def run() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
