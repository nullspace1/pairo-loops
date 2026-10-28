"""Configure the standalone developer example agent."""

from pathlib import Path

from pairo import (
    AgentConfiguration,
    ApprovalMode,
    CodexAgent,
    ReasoningEffort,
    SandboxPermission,
    WebSearchMode,
)
from pairo.tools.workspace_factory import WorkspaceFactory
from pairo.ui.permissions import TextualPermissionRequester


def get_isolated_developer(pipeline, repository):
    """Build a developer for direct tasks that do not use SpecManager."""
    temp = Path("./tmp/agents/isolated_developer")
    agent_directory = Path(__file__).resolve().parent
    workspace_factory = WorkspaceFactory(temp, SandboxPermission.WORKSPACE_WRITE)
    workspace_factory.add_workspace(Path("./src"), SandboxPermission.WORKSPACE_WRITE)
    workspace_factory.add_workspace(Path("./example"), SandboxPermission.WORKSPACE_WRITE)

    configuration = AgentConfiguration(
        name="isolated_developer",
        description=(
            "Implements focused, directly assigned development tasks outside the "
            "planner and SpecManager workflow."
        ),
        sandbox=SandboxPermission.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.USER,
        permission_requester=TextualPermissionRequester(pipeline, repository),
        workspace=temp,
        model="gpt-6.1-terra",
        base_instructions=(agent_directory / "isolated_developer.md").read_text(
            encoding="utf-8"
        ),
        reasoning_effort=ReasoningEffort.MEDIUM,
        web_search=WebSearchMode.DISABLED,
    )
    workspace_factory.configure(configuration)
    return CodexAgent(configuration, pipeline)
