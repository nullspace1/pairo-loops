"""Configure the independent verifier example agent."""

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


def get_verifier(pipeline, repository):
    """Build a read-only reviewer with an isolated writable test directory."""
    temp = Path("./tmp/agents/verifier")
    agent_directory = Path(__file__).resolve().parent
    workspace_factory = WorkspaceFactory(temp, SandboxPermission.WORKSPACE_WRITE)
    workspace_factory.add_workspace(Path("./src"), SandboxPermission.READ_ONLY)
    workspace_factory.add_workspace(Path("./example"), SandboxPermission.READ_ONLY)

    configuration = AgentConfiguration(
        name="verifier",
        sandbox=SandboxPermission.READ_ONLY,
        approval_mode=ApprovalMode.USER,
        permission_requester=TextualPermissionRequester(pipeline, repository),
        workspace=temp,
        model="gpt-5.6-terra",
        base_instructions=(agent_directory / "verifier.md").read_text(encoding="utf-8"),
        reasoning_effort=ReasoningEffort.MEDIUM,
        web_search=WebSearchMode.DISABLED,
    )
    workspace_factory.configure(configuration)
    return CodexAgent(configuration, pipeline)
