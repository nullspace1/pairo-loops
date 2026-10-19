"""Focused contracts for the asynchronous Codex agent adapter."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from pairo.core.agent import (
    AgentConfiguration,
    Hook,
    HookType,
    ReasoningEffort,
    SandboxPermission,
    WebSearchMode,
)
from pairo.core.clients import AgentStatus
from pairo.core.clients.codex_client import (
    CodexAgent,
    CodexSessionIdentifier,
    _ApprovalAwareAsyncCodexClient,
)
from pairo.core.events import ClientTokenUsageUpdated, EventPipeline, SessionCompacted
from pairo.core.mcp.mcp_permissions import MCPPermissions, MCPToolApprovalMode
from pairo.core.mcp.servers import HttpMCPServer, StdioMCPServer


class FakeAsyncCodexClient:
    def __init__(self) -> None:
        self.started = 0
        self.initialized = 0
        self.closed = 0
        self.compacted: list[str] = []

    async def start(self) -> None:
        self.started += 1

    async def initialize(self) -> None:
        self.initialized += 1

    async def close(self) -> None:
        self.closed += 1

    async def thread_start(self, _params):
        return SimpleNamespace(thread=SimpleNamespace(id="thread-1"))

    async def thread_compact(self, thread_id: str) -> None:
        self.compacted.append(thread_id)


def configuration(tmp_path: Path) -> AgentConfiguration:
    return AgentConfiguration(
        name="developer",
        sandbox=SandboxPermission.READ_ONLY,
        workspace=tmp_path,
        model="test",
    )


def test_codex_configuration_maps_reasoning_search_permissions_and_hooks(
    tmp_path: Path,
) -> None:
    writable_folder = tmp_path / "shared-output"
    writable_folder.mkdir()
    configured = AgentConfiguration(
        name="developer",
        sandbox=SandboxPermission.WORKSPACE_WRITE,
        workspace=tmp_path,
        model="gpt-test",
        reasoning_effort=ReasoningEffort.HIGH,
        web_search=WebSearchMode.LIVE,
        folder_permissions={
            writable_folder: SandboxPermission.WORKSPACE_WRITE,
            tmp_path: SandboxPermission.READ_ONLY,
        },
        mcp_servers=[
            HttpMCPServer(
                name="remote",
                url="https://example.test/mcp",
                permissions=MCPPermissions(
                    default_tools_approval_mode=MCPToolApprovalMode.PROMPT,
                    tool_approval_modes={"lookup": MCPToolApprovalMode.AUTO},
                ),
            ),
            StdioMCPServer(name="local", command="server", args=("--stdio",)),
        ],
        hooks={HookType.SESSION_START: [Hook(command="prepare", is_async=True)]},
    )

    async def convert() -> dict:
        pipeline = EventPipeline()
        agent = CodexAgent(configured, pipeline, FakeAsyncCodexClient())
        result = agent._codex_config(configured)
        await agent.close()
        return result

    result = asyncio.run(convert())

    assert result["model_reasoning_effort"] == "high"
    assert result["web_search"] == "live"
    assert result["sandbox_workspace_write"] == {
        "writable_roots": [str(writable_folder.resolve())]
    }
    assert result["mcp_servers"]["remote"] == {
        "url": "https://example.test/mcp",
        "startup_timeout_sec": 10.0,
        "http_headers": {"X-Agent-ID": str(configured.id)},
        "default_tools_approval_mode": "prompt",
        "tools": {"lookup": {"approval_mode": "auto"}},
    }
    assert result["mcp_servers"]["local"]["env"] == {
        "X-Agent-ID": str(configured.id)
    }
    assert result["hooks"]["SessionStart"][0]["hooks"][0] == {
        "type": "command",
        "command": "prepare",
        "async": True,
    }


def test_codex_identifier_is_durable_and_contains_no_turn_id() -> None:
    identifier = CodexSessionIdentifier(thread_id="thread-1")
    assert identifier.model_dump(mode="json") == {
        "provider": "codex",
        "thread_id": "thread-1",
    }
    with pytest.raises(ValueError):
        CodexSessionIdentifier.model_validate(
            {"provider": "codex", "thread_id": "thread-1", "turn_id": "turn-1"}
        )


def test_async_sdk_adapter_injects_the_sync_approval_handler() -> None:
    def approve(_method, _params):
        return {"decision": "accept"}

    client = _ApprovalAwareAsyncCodexClient(approve)

    assert client._sync._approval_handler is approve


def test_collect_text_recurses_in_document_order() -> None:
    value = {
        "text": "outer",
        "turns": [
            {"items": [{"text": "first"}]},
            {"items": [{"nested": {"text": "final"}}]},
        ],
    }
    async def collect() -> list[str]:
        pipeline = EventPipeline()
        agent = CodexAgent(
            AgentConfiguration(
                name="test",
                sandbox=SandboxPermission.READ_ONLY,
                workspace=".",
                model="test",
            ),
            pipeline,
            FakeAsyncCodexClient(),
        )
        result = agent._collect_text(value)
        await agent.close()
        return result

    assert asyncio.run(collect()) == ["outer", "first", "final"]


def test_client_initialization_and_compaction_are_awaited_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        client = FakeAsyncCodexClient()
        agent = CodexAgent(configuration(tmp_path), pipeline, client)

        await asyncio.gather(*(agent._ensure_client() for _ in range(4)))
        active = await agent.new_session("Conversation 1")
        compacted = asyncio.create_task(pipeline.listen(SessionCompacted))
        await asyncio.sleep(0)
        await agent.compact()

        assert client.started == 1
        assert client.initialized == 1
        assert client.compacted == [active.identifier.thread_id]
        assert agent.status() is AgentStatus.IDLE
        assert isinstance(await compacted, SessionCompacted)

        await agent.close()
        await agent.close()
        assert client.closed == 1

    asyncio.run(scenario())


def test_token_usage_notification_is_provider_neutral(tmp_path: Path) -> None:
    from openai_codex.generated.v2_all import (
        ThreadTokenUsage,
        ThreadTokenUsageUpdatedNotification,
        TokenUsageBreakdown,
    )

    def breakdown(total: int) -> TokenUsageBreakdown:
        return TokenUsageBreakdown(
            inputTokens=total - 20,
            cachedInputTokens=10,
            outputTokens=20,
            reasoningOutputTokens=5,
            totalTokens=total,
        )

    async def scenario() -> None:
        pipeline = EventPipeline()
        agent = CodexAgent(configuration(tmp_path), pipeline, FakeAsyncCodexClient())
        event = await agent._translate_notification(
            CodexSessionIdentifier(thread_id="thread-1"),
            SimpleNamespace(
                payload=ThreadTokenUsageUpdatedNotification(
                    threadId="thread-1",
                    turnId="turn-1",
                    tokenUsage=ThreadTokenUsage(
                        last=breakdown(100),
                        total=breakdown(500),
                        modelContextWindow=200_000,
                    ),
                )
            ),
        )
        assert isinstance(event, ClientTokenUsageUpdated)
        assert event.last.total_tokens == 100
        assert event.total.total_tokens == 500
        await agent.close()

    asyncio.run(scenario())
