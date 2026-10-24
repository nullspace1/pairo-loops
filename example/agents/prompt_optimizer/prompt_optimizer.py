"""Configure the execution-history optimizer example agent."""

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


def get_prompt_optimizer(pipeline, repository):
    """Build an agent that improves the example from prior execution evidence."""
    temp = Path("./tmp/agents/prompt_optimizer")
    agent_directory = Path(__file__).resolve().parent
    workspace_factory = WorkspaceFactory(temp, SandboxPermission.WORKSPACE_WRITE)
    workspace_factory.add_workspace(Path("./.log"), SandboxPermission.READ_ONLY)
    workspace_factory.add_workspace(Path("./example"), SandboxPermission.WORKSPACE_WRITE)

    configuration = AgentConfiguration(
        name="prompt_optimizer",
        description=(
            "Analyzes prior execution events and improves prompts, context, "
            "workflows, tools, and related example assets."
        ),
        sandbox=SandboxPermission.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.USER,
        permission_requester=TextualPermissionRequester(pipeline, repository),
        workspace=temp,
        model="gpt-5.6-sol",
        base_instructions=(agent_directory / "prompt_optimizer.md").read_text(
            encoding="utf-8"
        ),
        reasoning_effort=ReasoningEffort.MEDIUM,
        web_search=WebSearchMode.DISABLED,
    )
    workspace_factory.configure(configuration)
    return CodexAgent(configuration, pipeline)
