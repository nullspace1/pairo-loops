"""Configure the developer example agent."""

from pathlib import Path

from pairo import (
    AgentConfiguration,
    ApprovalMode,
    CodexAgent,
    ReasoningEffort,
    SandboxPermission,
    WebSearchMode,
)
from pairo.ui.permissions import TextualPermissionRequester


def get_developer(name, pipeline, repository):
    agent_directory = Path(__file__).resolve().parent
    instructions = (agent_directory / "developer.md").read_text(encoding="utf-8")

    configuration = AgentConfiguration(
        name=name,
        sandbox=SandboxPermission.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.USER,
        permission_requester=TextualPermissionRequester(pipeline, repository),
        workspace=agent_directory.parents[2] / "src",
        model="gpt-5.6-terra",
        skill_folders=(agent_directory / "skills",),
        base_instructions=instructions,
        reasoning_effort=ReasoningEffort.MEDIUM,
        web_search=WebSearchMode.DISABLED,
    )
    return CodexAgent(configuration, pipeline)
