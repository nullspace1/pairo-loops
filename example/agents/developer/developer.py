"""Configure the developer example agent."""

from pathlib import Path

from pairo import (
    Agent,
    AgentConfiguration,
    ApprovalMode,
    CodexAgentClient,
    ReasoningEffort,
    Sandbox,
    WebSearchMode,
)
from pairo.ui.permissions import TextualPermissionRequester


def get_developer(pipeline, repository, servers):
    agent_directory = Path(__file__).resolve().parent
    instructions = (agent_directory / "developer.md").read_text(encoding="utf-8")

    return Agent(
        name="developer",
        event_pipeline=pipeline,
        client=CodexAgentClient(),
        config=AgentConfiguration(
            sandbox=Sandbox.WORKSPACE_WRITE,
            approval_mode=ApprovalMode.USER,
            permission_requester=TextualPermissionRequester(pipeline, repository),
            workspace=agent_directory.parents[2] / "src",
            model="gpt-5.6-terra",
            skill_folder=agent_directory / "skills",
            base_instructions=instructions,
            reasoning_effort=ReasoningEffort.LOW,
            web_search=WebSearchMode.DISABLED,
            mcp_servers=servers,
        ),
    )
