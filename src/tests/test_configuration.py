"""Provider-neutral configuration and Codex mapping tests."""

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from pairo.core.agent_config import (
    AgentConfiguration,
    ApprovalMode,
    Hook,
    HookType,
    HttpMCPServer,
    ReasoningEffort,
    Sandbox,
    StdioMCPServer,
    WebSearchMode,
)
from pairo.core.clients.codex_client import CodexAgentClient
from pairo.core.mcp.mcp_permissions import MCPPermissions, MCPToolApprovalMode


def test_configuration_defaults_are_explicit_and_isolated(tmp_path: Path) -> None:
    first = AgentConfiguration(
        sandbox=Sandbox.READ_ONLY,
        workspace=tmp_path,
        model=" model ",
    )
    second = AgentConfiguration(
        sandbox=Sandbox.READ_ONLY,
        workspace=tmp_path,
        model="other",
    )

    assert first.workspace == tmp_path.resolve()
    assert first.model == "model"
    assert first.prompt is None
    assert first.instructions is None
    assert first.reasoning_effort is None
    assert first.web_search is None
    assert first.approval_mode is ApprovalMode.AUTO
    assert first.permission_requester is None
    assert first.mcp_servers == []
    assert first.skill_folder is None
    assert first.hooks == {}
    assert first.mcp_servers is not second.mcp_servers
    assert first.hooks is not second.hooks


def test_configuration_validates_model_skill_folder_and_interactive_mode(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="model cannot be empty"):
        AgentConfiguration(sandbox=Sandbox.READ_ONLY, workspace=tmp_path, model=" ")
    with pytest.raises(ValidationError, match="existing directory"):
        AgentConfiguration(
            sandbox=Sandbox.READ_ONLY,
            workspace=tmp_path,
            model="test",
            skill_folder=tmp_path / "missing",
        )
    with pytest.raises(ValidationError, match="requires a permission requester"):
        AgentConfiguration(
            sandbox=Sandbox.READ_ONLY,
            workspace=tmp_path,
            model="test",
            approval_mode=ApprovalMode.USER,
        )


def test_hooks_accept_field_name_and_alias_and_reject_unknown_fields() -> None:
    assert Hook(command=" pytest ", is_async=True) == Hook.model_validate(
        {"command": "pytest", "async": True}
    )
    with pytest.raises(ValidationError, match="Hook command cannot be empty"):
        Hook(command=" ", is_async=False)
    with pytest.raises(ValidationError, match="Extra inputs"):
        Hook(command="pytest", is_async=False, unexpected=True)


def test_mcp_server_defaults_descriptions_and_validation() -> None:
    http = HttpMCPServer(name=" remote ", url=" https://example.test/mcp ")
    stdio = StdioMCPServer(name=" local ", command=" runner ", args=("--flag",))

    assert http.name == "remote"
    assert http.description == "https://example.test/mcp"
    assert http.startup_timeout_sec == 10.0
    assert http.permissions == MCPPermissions()
    assert stdio.description == "runner --flag"
    assert stdio.args == ("--flag",)
    assert stdio.env == {}
    assert stdio.permissions is None
    for field, value in (("name", ""), ("url", " ")):
        values = {"name": "remote", "url": "https://example.test/mcp", field: value}
        with pytest.raises(ValidationError, match="cannot be empty"):
            HttpMCPServer(**values)
    with pytest.raises(ValidationError):
        StdioMCPServer(name="local", command="runner", startup_timeout_sec=0)


def test_configuration_rejects_duplicate_mcp_names(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="names must be unique"):
        AgentConfiguration(
            sandbox=Sandbox.READ_ONLY,
            workspace=tmp_path,
            model="test",
            mcp_servers=[
                StdioMCPServer(name="same", command="one"),
                StdioMCPServer(name="same", command="two"),
            ],
        )


def test_mcp_permissions_normalize_names_and_serialize_modes() -> None:
    permissions = MCPPermissions(
        default_tools_approval_mode=MCPToolApprovalMode.PROMPT,
        tool_approval_modes={" write_file ": MCPToolApprovalMode.WRITES},
    )
    assert permissions.tool_approval_modes == {
        "write_file": MCPToolApprovalMode.WRITES
    }
    with pytest.raises(ValidationError, match="require a tool name"):
        MCPPermissions(tool_approval_modes={" ": MCPToolApprovalMode.AUTO})


def test_codex_adapter_installs_skills_in_the_workspace(tmp_path: Path) -> None:
    skill_folder = tmp_path / "shared-skills"
    skill = skill_folder / "review-code"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Review code", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    configuration = AgentConfiguration(
        sandbox=Sandbox.READ_ONLY,
        workspace=workspace,
        model="gpt-test",
        skill_folder=skill_folder,
    )
    adapter = CodexAgentClient()

    asyncio.run(adapter._apply_configuration(configuration))

    assert (
        workspace / ".codex" / "skills" / "review-code" / "SKILL.md"
    ).read_text(encoding="utf-8") == "# Review code"


def test_neutral_core_modules_do_not_import_codex() -> None:
    root = Path(__file__).parents[1] / "pairo" / "core"
    neutral_files = [
        "agent.py",
        "agent_config.py",
        "agent_server.py",
        "mcp/mcp_permissions.py",
        "mcp/servers.py",
        "events/agent_events.py",
    ]
    for relative in neutral_files:
        assert "openai_codex" not in (root / relative).read_text(encoding="utf-8")


def test_codex_adapter_serializes_mcp_environments(tmp_path: Path) -> None:
    configuration = AgentConfiguration(
        sandbox=Sandbox.READ_ONLY,
        workspace=tmp_path,
        model="gpt-test",
        mcp_servers=[
            HttpMCPServer(
                name="remote",
                url="https://example.com/mcp",
                env={"Authorization": "MCP_ACCESS_TOKEN"},
            ),
            StdioMCPServer(
                name="local",
                command="mcp-server",
                env={"MCP_ACCESS_TOKEN": "secret"},
            ),
        ],
    )

    servers = CodexAgentClient._codex_config(
        configuration,
        agent_name="planner",
    )["mcp_servers"]

    assert servers["remote"]["env_http_headers"] == {
        "Authorization": "MCP_ACCESS_TOKEN"
    }
    assert servers["remote"]["http_headers"] == {"X-Agent-NAME": "planner"}
    assert servers["local"]["env"] == {
        "MCP_ACCESS_TOKEN": "secret",
        "X-Agent-NAME": "planner",
    }
