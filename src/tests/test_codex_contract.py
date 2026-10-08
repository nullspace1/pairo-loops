"""Focused contract tests for provider-neutral-to-Codex mapping helpers."""

from pathlib import Path

import pytest

from pairo.core.agent_config import (
    AgentConfiguration,
    Hook,
    HookType,
    ReasoningEffort,
    Sandbox,
    WebSearchMode,
)
from pairo.core.clients.codex_client import CodexAgentClient, CodexSessionIdentifier
from pairo.core.mcp.mcp_permissions import MCPPermissions, MCPToolApprovalMode
from pairo.core.mcp.servers import HttpMCPServer, StdioMCPServer


def test_codex_configuration_maps_reasoning_search_permissions_and_hooks(
    tmp_path: Path,
) -> None:
    configuration = AgentConfiguration(
        sandbox=Sandbox.WORKSPACE_WRITE,
        workspace=tmp_path,
        model="gpt-test",
        reasoning_effort=ReasoningEffort.HIGH,
        web_search=WebSearchMode.LIVE,
        mcp_servers=[
            HttpMCPServer(
                name="remote",
                url="https://example.test/mcp",
                permissions=MCPPermissions(
                    default_tools_approval_mode=MCPToolApprovalMode.PROMPT,
                    tool_approval_modes={"lookup": MCPToolApprovalMode.AUTO},
                ),
            ),
            StdioMCPServer(
                name="local",
                command="server",
                args=("--stdio",),
                permissions=MCPPermissions(default_tools_approval_mode=None),
            ),
        ],
        hooks={
            HookType.SESSION_START: [Hook(command="prepare", is_async=True)],
        },
    )

    result = CodexAgentClient._codex_config(configuration, agent_name="developer")
    assert result["model_reasoning_effort"] == "high"
    assert result["web_search"] == "live"
    assert result["mcp_servers"]["remote"] == {
        "url": "https://example.test/mcp",
        "startup_timeout_sec": 10.0,
        "http_headers": {"X-Agent-NAME": "developer"},
        "default_tools_approval_mode": "prompt",
        "tools": {"lookup": {"approval_mode": "auto"}},
    }
    assert result["mcp_servers"]["local"] == {
        "command": "server",
        "args": ["--stdio"],
        "startup_timeout_sec": 10.0,
        "env": {"X-Agent-NAME": "developer"},
    }
    assert result["hooks"] == {
        "SessionStart": [
            {
                "hooks": [
                    {"type": "command", "command": "prepare", "async": True}
                ]
            }
        ]
    }


def test_codex_config_omits_unconfigured_optional_values(tmp_path: Path) -> None:
    configuration = AgentConfiguration(
        sandbox=Sandbox.READ_ONLY,
        workspace=tmp_path,
        model="test",
    )
    assert CodexAgentClient._codex_config(configuration) == {}


def test_codex_identifier_requires_an_active_turn_for_turn_operations() -> None:
    inactive = CodexSessionIdentifier(thread_id="thread-1")
    with pytest.raises(ValueError, match="no active turn"):
        CodexAgentClient._active_identifier(inactive)
    active = inactive.model_copy(update={"turn_id": "turn-1"})
    assert CodexAgentClient._active_identifier(active) is active


def test_collect_text_recurses_in_document_order() -> None:
    value = {
        "text": "outer",
        "turns": [
            {"items": [{"text": "first"}]},
            {"items": [{"nested": {"text": "final"}}]},
        ],
    }
    assert CodexAgentClient._collect_text(value) == ["outer", "first", "final"]


def test_permission_route_declines_when_no_runtime_requester_is_available() -> None:
    adapter = CodexAgentClient()
    assert adapter._route_permission(
        "item/commandExecution/requestApproval", {"command": "pytest"}
    ) == {"decision": "decline"}
