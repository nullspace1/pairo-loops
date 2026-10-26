"""Configure the agent-documentation specialist."""

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


def get_agent_documenter(pipeline, repository):
    """Build an agent that creates verified user and agent documentation."""
    project_root = Path(__file__).resolve().parents[3]
    agent_directory = Path(__file__).resolve().parent

    configuration = AgentConfiguration(
        name="agent_documenter",
        description=(
            "Creates verified user documentation and diagrams, and maintains "
            "the prompts and skills needed to explain agent workflows."
        ),
        sandbox=SandboxPermission.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.USER,
        permission_requester=TextualPermissionRequester(pipeline, repository),
        workspace=project_root,
        model="gpt-5.6-luna",
        skill_folders=(agent_directory / "skills",),
        base_instructions=(agent_directory / "agent_documenter.md").read_text(
            encoding="utf-8"
        ),
        reasoning_effort=ReasoningEffort.MEDIUM,
        web_search=WebSearchMode.DISABLED,
    )
    return CodexAgent(configuration, pipeline)
