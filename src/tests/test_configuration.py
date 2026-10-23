"""Agent configuration mutation and validation tests."""

from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from pairo.core.agent import (
    AgentConfiguration,
    ApprovalMode,
    Hook,
    HookType,
    ReasoningEffort,
    SandboxPermission,
    WebSearchMode,
)
from pairo.core.mcp.mcp_permissions import MCPPermissions, MCPToolApprovalMode
from pairo.core.mcp.servers import HttpMCPServer, StdioMCPServer
from pairo.core.permissions import PermissionDecision, PermissionRequester


class AllowRequester(PermissionRequester):
    async def request(self, request):
        return PermissionDecision.ACCEPT


def make_agent(tmp_path: Path, **changes) -> AgentConfiguration:
    values = {
        "name": " agent ",
        "sandbox": SandboxPermission.READ_ONLY,
        "workspace": tmp_path,
        "model": " model ",
    }
    values.update(changes)
    return AgentConfiguration(**values)


def test_agent_defaults_identity_and_collection_isolation(tmp_path: Path) -> None:
    first = make_agent(tmp_path)
    equivalent = make_agent(tmp_path)
    second = make_agent(tmp_path, model="other")
    differently_named = make_agent(tmp_path, name="other agent")

    assert isinstance(first.id, UUID)
    assert first.id == equivalent.id
    assert first.id == second.id
    assert first.id != differently_named.id
    assert first.name == "agent"
    assert first.workspace == tmp_path.resolve()
    assert first.model == "model"
    assert first.reasoning_effort is ReasoningEffort.LOW
    assert first.web_search is WebSearchMode.CACHED
    assert first.approval_mode is ApprovalMode.AUTO
    assert first.mcp_servers == ()
    assert first.skill_folders == ()
    assert dict(first.hooks) == {}
    assert dict(first.folder_permissions) == {}
    assert first.mcp_servers is not second.mcp_servers or first.mcp_servers == ()

    with pytest.raises(ValidationError, match="frozen"):
        first.name = "renamed"
    with pytest.raises(ValidationError, match="frozen"):
        first.id = second.id
    with pytest.raises(ValidationError, match="frozen"):
        first.model = "direct-assignment"


def test_agent_name_is_required_to_derive_its_id(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="name"):
        AgentConfiguration(
            sandbox=SandboxPermission.READ_ONLY,
            workspace=tmp_path,
            model="model",
        )


def test_scalar_setters_validate_and_commit(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    requester = AllowRequester()

    agent.set_description("Reviewer")
    agent.set_sandbox(SandboxPermission.WORKSPACE_WRITE)
    agent.set_workspace(other_workspace)
    agent.set_model(" updated ")
    agent.set_base_instructions("base")
    agent.set_developer_instructions("developer")
    agent.set_reasoning_effort(ReasoningEffort.HIGH)
    agent.set_web_search(WebSearchMode.LIVE)
    agent.set_permission_requester(requester)
    agent.set_approval_mode(ApprovalMode.USER)

    assert agent.description == "Reviewer"
    assert agent.sandbox is SandboxPermission.WORKSPACE_WRITE
    assert agent.workspace == other_workspace.resolve()
    assert agent.model == "updated"
    assert agent.base_instructions == "base"
    assert agent.developer_instructions == "developer"
    assert agent.reasoning_effort is ReasoningEffort.HIGH
    assert agent.web_search is WebSearchMode.LIVE
    assert agent.approval_mode is ApprovalMode.USER
    assert agent.permission_requester is requester


def test_instruction_appenders_preserve_existing_blocks(tmp_path: Path) -> None:
    agent = make_agent(tmp_path, base_instructions="base", developer_instructions="dev")

    agent.append_base_instructions(" more base ")
    agent.append_developer_instructions(" more dev ")
    agent.append_base_instructions("   ")

    assert agent.base_instructions == "base\n\nmore base"
    assert agent.developer_instructions == "dev\n\nmore dev"


def test_invalid_setter_is_atomic_and_preserves_cross_field_rules(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    with pytest.raises(ValidationError, match="cannot be empty"):
        agent.set_model(" ")
    assert agent.model == "model"

    with pytest.raises(ValidationError, match="existing directory"):
        agent.set_workspace(tmp_path / "missing")
    assert agent.workspace == tmp_path.resolve()

    with pytest.raises(ValidationError, match="requires a permission requester"):
        agent.set_approval_mode(ApprovalMode.USER)
    assert agent.approval_mode is ApprovalMode.AUTO

    agent.set_permission_requester(AllowRequester())
    agent.set_approval_mode(ApprovalMode.USER)
    with pytest.raises(ValidationError, match="requires a permission requester"):
        agent.set_permission_requester(None)
    assert agent.permission_requester is not None


def test_mcp_skill_and_hook_mutators_expose_immutable_collections(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    skill_one = tmp_path / "skills-one"
    skill_two = tmp_path / "skills-two"
    skill_one.mkdir()
    skill_two.mkdir()
    http = HttpMCPServer(name="remote", url="https://example.test/mcp")
    stdio = StdioMCPServer(name="local", command="server")
    hook = Hook(command="prepare", is_async=True)

    agent.add_mcp_server(http)
    agent.add_mcp_server(stdio)
    assert agent.mcp_servers == (http, stdio)
    with pytest.raises(ValidationError, match="names must be unique"):
        agent.add_mcp_server(HttpMCPServer(name="remote", url="https://other.test"))
    assert agent.mcp_servers == (http, stdio)
    agent.remove_mcp_server("remote")
    assert agent.mcp_servers == (stdio,)

    agent.set_skill_folders([skill_one])
    agent.add_skill_folder(skill_two)
    assert agent.skill_folders == (skill_one.resolve(), skill_two.resolve())
    agent.remove_skill_folder(skill_one)
    assert agent.skill_folders == (skill_two.resolve(),)

    agent.add_hook(HookType.SESSION_START, hook)
    assert agent.hooks[HookType.SESSION_START] == (hook,)
    with pytest.raises(TypeError):
        agent.hooks[HookType.STOP] = (hook,)
    agent.remove_hook(HookType.SESSION_START, hook)
    assert dict(agent.hooks) == {}


def test_folder_permission_mutators_normalize_and_isolate_paths(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    writable = tmp_path / "writable"
    readable = tmp_path / "readable"
    writable.mkdir()
    readable.mkdir()

    agent.add_folder_permission(writable, SandboxPermission.WORKSPACE_WRITE)
    agent.add_folder_permission(readable, SandboxPermission.READ_ONLY)

    assert dict(agent.folder_permissions) == {
        writable.resolve(): SandboxPermission.WORKSPACE_WRITE,
        readable.resolve(): SandboxPermission.READ_ONLY,
    }
    with pytest.raises(TypeError):
        agent.folder_permissions[tmp_path] = SandboxPermission.DANGER_FULL_ACCESS

    agent.remove_folder_permission(readable)
    assert dict(agent.folder_permissions) == {
        writable.resolve(): SandboxPermission.WORKSPACE_WRITE
    }

    with pytest.raises(ValidationError, match="existing directories"):
        agent.add_folder_permission(tmp_path / "missing", SandboxPermission.READ_ONLY)
    assert dict(agent.folder_permissions) == {
        writable.resolve(): SandboxPermission.WORKSPACE_WRITE
    }


def test_snapshot_is_independent_from_later_mutation(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    snapshot = agent.snapshot()
    agent.set_model("new-model")
    assert snapshot.id == agent.id
    assert snapshot.model == "model"
    assert agent.model == "new-model"


def test_hooks_and_mcp_models_keep_their_validation(tmp_path: Path) -> None:
    hook = Hook(command=" pytest ", is_async=True)
    assert hook.command == "pytest"
    with pytest.raises(ValidationError, match="cannot be empty"):
        Hook(command=" ", is_async=False)

    permissions = MCPPermissions(
        default_tools_approval_mode=MCPToolApprovalMode.PROMPT,
        tool_approval_modes={" write_file ": MCPToolApprovalMode.WRITES},
    )
    assert permissions.tool_approval_modes == {
        "write_file": MCPToolApprovalMode.WRITES
    }


def test_neutral_core_modules_do_not_import_codex() -> None:
    root = Path(__file__).parents[1] / "pairo" / "core"
    neutral_files = [
        "agent/agent.py",
        "clients/client.py",
        "mcp/mcp_permissions.py",
        "mcp/servers.py",
        "events/agent_events.py",
    ]
    for relative in neutral_files:
        assert "openai_codex" not in (root / relative).read_text(encoding="utf-8")
