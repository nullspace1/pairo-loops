"""Focused contract tests for provider-neutral-to-Codex mapping helpers."""

import asyncio
from pathlib import Path

import pytest

from pairo.core.agent import (
    Agent,
    Hook,
    HookType,
    ReasoningEffort,
    SandboxMode,
    WebSearchMode,
)
from pairo.core.clients.codex_client import CodexAgentClient, CodexSessionIdentifier
from pairo.core.mcp.mcp_permissions import MCPPermissions, MCPToolApprovalMode
from pairo.core.mcp.servers import HttpMCPServer, StdioMCPServer


def test_codex_configuration_maps_reasoning_search_permissions_and_hooks(
    tmp_path: Path,
) -> None:
    configuration = Agent(
        name="developer",
        sandbox=SandboxMode.WORKSPACE_WRITE,
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

    result = CodexAgentClient._codex_config(configuration)
    assert result["model_reasoning_effort"] == "high"
    assert result["web_search"] == "live"
    assert result["mcp_servers"]["remote"] == {
        "url": "https://example.test/mcp",
        "startup_timeout_sec": 10.0,
        "http_headers": {"X-Agent-ID": str(configuration.id)},
        "default_tools_approval_mode": "prompt",
        "tools": {"lookup": {"approval_mode": "auto"}},
    }
    assert result["mcp_servers"]["local"] == {
        "command": "server",
        "args": ["--stdio"],
        "startup_timeout_sec": 10.0,
        "env": {"X-Agent-ID": str(configuration.id)},
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
    configuration = Agent(
        name="reader",
        sandbox=SandboxMode.READ_ONLY,
        workspace=tmp_path,
        model="test",
    )
    assert CodexAgentClient._codex_config(configuration) == {
        "model_reasoning_effort": "low",
        "web_search": "cached",
    }


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
        "item/commandExecution/requestApproval",
        {"threadId": "unknown-thread", "command": "pytest"},
    ) == {"decision": "decline"}


def test_concurrent_initialization_creates_one_sdk_client(monkeypatch) -> None:
    class FakeSdkClient:
        instances: list["FakeSdkClient"] = []

        def __init__(self, approval_handler) -> None:
            self.approval_handler = approval_handler
            self.close_calls = 0
            self.instances.append(self)

        def start(self) -> None:
            return None

        def initialize(self) -> None:
            return None

        def close(self) -> None:
            self.close_calls += 1

    async def scenario() -> None:
        monkeypatch.setattr(
            "pairo.core.clients.codex_client.CodexClient",
            FakeSdkClient,
        )
        adapter = CodexAgentClient()
        clients = await asyncio.gather(*(adapter._ensure_client() for _ in range(4)))
        assert len(FakeSdkClient.instances) == 1
        assert all(client is clients[0] for client in clients)
        await adapter.close()
        await adapter.close()
        assert FakeSdkClient.instances[0].close_calls == 1

    asyncio.run(scenario())
