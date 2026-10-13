"""Configure the intern example agent."""

from pathlib import Path

from pairo import (
    Agent,
    ApprovalMode,
    ReasoningEffort,
    SandboxMode,
    WebSearchMode,
)
from pairo.ui.permissions import TextualPermissionRequester


def get_intern(pipeline, repository):
    agent_directory = Path(__file__).resolve().parent
    instructions = (agent_directory / "intern.md").read_text(encoding="utf-8")

    return Agent(
        name="intern",
        sandbox=SandboxMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.USER,
        permission_requester=TextualPermissionRequester(pipeline, repository),
        workspace=agent_directory.parents[2] / "src",
        model="gpt-5.6-luna",
        skill_folders=(agent_directory / "skills",),
        base_instructions=instructions,
        reasoning_effort=ReasoningEffort.LOW,
        web_search=WebSearchMode.DISABLED,
    )
