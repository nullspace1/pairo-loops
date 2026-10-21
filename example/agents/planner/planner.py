"""Configure the planner example agent."""

from pathlib import Path

from pairo import (
    AgentConfiguration,
    ApprovalMode,
    CodexAgent,
    ReasoningEffort,
    SandboxPermission,
    WebSearchMode,
)
from pairo.core.tools.workspace_factory import WorkspaceFactory
from pairo.ui.permissions import TextualPermissionRequester


def get_planner(pipeline, repository):
    temp = Path("./tmp/planner")
    
    agent_directory = Path(__file__).resolve().parent
    
    instructions = (agent_directory / "planner.md").read_text(encoding="utf-8")
    
    workspace_factory = WorkspaceFactory(temp, SandboxPermission.WORKSPACE_WRITE)
    
    workspace_factory.add_workspace(
        Path("./src"), SandboxPermission.READ_ONLY
    )

    configuration = AgentConfiguration(
        name="planner",
        sandbox=SandboxPermission.READ_ONLY,
        approval_mode=ApprovalMode.USER,
        permission_requester=TextualPermissionRequester(pipeline, repository),
        workspace=temp,
        model="gpt-5.6-sol",
        skill_folders=(agent_directory / "skills",),
        base_instructions=instructions + "\n Ignore invariant checking on the verification for now.",
        reasoning_effort=ReasoningEffort.MEDIUM,
        web_search=WebSearchMode.CACHED,
    )
    
    workspace_factory.configure(configuration)
    
    return CodexAgent(configuration, pipeline)
