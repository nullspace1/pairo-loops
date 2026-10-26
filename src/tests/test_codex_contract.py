"""Focused contracts for the asynchronous Codex agent adapter."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from openai_codex.generated.v2_all import (
    ContextCompactedNotification,
    CommandExecutionOutputDeltaNotification,
    CommandExecutionStatus,
    CommandExecutionThreadItem,
    FileChangeOutputDeltaNotification,
    ItemStartedNotification,
    McpToolCallProgressNotification,
    McpToolCallResult,
    McpToolCallError,
    McpToolCallStatus,
    McpToolCallThreadItem,
)
from pydantic import BaseModel

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
from pairo.core.events import (
    AgentError,
    ClientMcpToolCallCompleted,
    ClientCommandExecutionCompleted,
    ClientTokenUsageUpdated,
    ClientRecognizedProviderNoOp,
    ClientUnknownProviderEvent,
    EventPipeline,
    MessageSent,
    SessionCompacted,
)
from pairo.core.mcp.mcp_permissions import MCPPermissions, MCPToolApprovalMode
from pairo.core.mcp.servers import HttpMCPServer, StdioMCPServer


class FakeAsyncCodexClient:
    def __init__(self) -> None:
        self.started = 0
        self.initialized = 0
        self.closed = 0
        self.compacted: list[str] = []
        self.notifications: asyncio.Queue[SimpleNamespace] = asyncio.Queue()
        self.compact_error: RuntimeError | None = None
        self.compaction_operations: list[SimpleNamespace] = []
        self.compaction_route_active_when_started = False

    async def start(self) -> None:
        self.started += 1

    async def initialize(self) -> None:
        self.initialized += 1

    async def close(self) -> None:
        self.closed += 1

    async def thread_start(self, _params):
        return SimpleNamespace(thread=SimpleNamespace(id="thread-1"))

    async def thread_compact(self, thread_id: str) -> None:
        self.compaction_route_active_when_started = bool(self.compaction_operations)
        if self.compact_error is not None:
            raise self.compact_error
        self.compacted.append(thread_id)

    async def next_notification(self):
        return await self.notifications.get()

    def register_goal_operation(self, thread_id: str) -> SimpleNamespace:
        operation = SimpleNamespace(thread_id=thread_id)
        self.compaction_operations.append(operation)
        return operation

    def unregister_goal_operation(self, operation: SimpleNamespace) -> None:
        self.compaction_operations.remove(operation)

    async def next_goal_notification(self, _operation: SimpleNamespace):
        return await self.notifications.get()


def configuration(tmp_path: Path) -> AgentConfiguration:
    return AgentConfiguration(
        id=uuid4(),
        name="developer",
        sandbox=SandboxPermission.READ_ONLY,
        workspace=tmp_path,
        model="test",
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (CommandExecutionStatus.in_progress, "inProgress"),
        (CommandExecutionStatus.completed, "completed"),
        (CommandExecutionStatus.failed, "failed"),
        (CommandExecutionStatus.declined, "declined"),
    ],
)
def test_command_completion_uses_sdk_wire_status(
    tmp_path: Path, status: CommandExecutionStatus, expected: str
) -> None:
    async def scenario() -> None:
        agent = CodexAgent(configuration(tmp_path), EventPipeline(), FakeAsyncCodexClient())
        event = agent._completed_activity(CommandExecutionThreadItem(
            id="command-1", type="commandExecution", command="pytest", cwd="C:/work",
            status=status, commandActions=[], exitCode=1, aggregatedOutput="failure output",
        ))

        assert isinstance(event, ClientCommandExecutionCompleted)
        assert event.status == expected
        assert event.exit_code == 1
        assert event.output == "failure output"
        assert "CommandExecutionStatus" not in event.model_dump_json()
        await agent.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (McpToolCallStatus.in_progress, "inProgress"),
        (McpToolCallStatus.completed, "completed"),
        (McpToolCallStatus.failed, "failed"),
    ],
)
def test_mcp_completion_uses_sdk_wire_status(
    tmp_path: Path, status: McpToolCallStatus, expected: str
) -> None:
    async def scenario() -> None:
        agent = CodexAgent(configuration(tmp_path), EventPipeline(), FakeAsyncCodexClient())
        event = agent._completed_activity(McpToolCallThreadItem(
            id="call-1", type="mcpToolCall", server="tools", tool="inspect",
            arguments={"path": "README.md"}, status=status,
            error=McpToolCallError(message="Provider unavailable"),
        ))

        assert isinstance(event, ClientMcpToolCallCompleted)
        assert event.status == expected
        assert event.arguments == {"path": "README.md"}
        assert event.error == "Provider unavailable"
        assert "McpToolCallStatus" not in event.model_dump_json()
        await agent.close()

    asyncio.run(scenario())


def test_mcp_completion_serializes_sdk_result_to_json(tmp_path: Path) -> None:
    async def scenario() -> None:
        agent = CodexAgent(
            configuration(tmp_path), EventPipeline(), FakeAsyncCodexClient()
        )
        item = McpToolCallThreadItem(
            id="call-1",
            type="mcpToolCall",
            server="spec-manager",
            tool="get_assignment",
            arguments={"include_history": True},
            status=McpToolCallStatus.completed,
            result=McpToolCallResult(
                content=[{"type": "text", "text": "ready"}],
                structuredContent={"status": "idle", "task": None},
            ),
        )

        event = agent._completed_activity(item)

        assert isinstance(event, ClientMcpToolCallCompleted)
        assert event.arguments == {"include_history": True}
        assert event.result == {
            "content": [{"type": "text", "text": "ready"}],
            "structuredContent": {"status": "idle", "task": None},
        }
        await agent.close()

    asyncio.run(scenario())


def test_codex_configuration_maps_reasoning_search_permissions_and_hooks(
    tmp_path: Path,
) -> None:
    writable_folder = tmp_path / "shared-output"
    writable_folder.mkdir()
    configured = AgentConfiguration(
        id=uuid4(),
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
                id=uuid4(),
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
        compacting = asyncio.create_task(agent.compact())
        await asyncio.sleep(0)

        assert agent.status() is AgentStatus.COMPACTING
        await client.notifications.put(SimpleNamespace(
            method="account/updated", payload=SimpleNamespace()
        ))
        await client.notifications.put(SimpleNamespace(
            method="thread/compacted",
            payload=ContextCompactedNotification(
                threadId=active.identifier.thread_id,
                turnId="compact-turn",
            )
        ))
        await compacting

        assert client.started == 1
        assert client.initialized == 1
        assert client.compacted == [active.identifier.thread_id]
        assert client.compaction_route_active_when_started
        assert client.compaction_operations == []
        assert agent.status() is AgentStatus.IDLE
        assert isinstance(await compacted, SessionCompacted)

        await agent.close()
        await agent.close()
        assert client.closed == 1

    asyncio.run(scenario())


def test_message_is_rejected_without_publication_during_compaction(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        client = FakeAsyncCodexClient()
        agent = CodexAgent(configuration(tmp_path), pipeline, client)
        active = await agent.new_session("Conversation 1")
        events: list[BaseModel] = []
        subscription = pipeline.subscribe(
            BaseModel, lambda event, _subscription: events.append(event)
        )
        compacting = asyncio.create_task(agent.compact())
        await asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="Compaction is in progress"):
            await agent.send_message("continue")

        await client.notifications.put(SimpleNamespace(
            method="thread/compacted",
            payload=ContextCompactedNotification(
                threadId=active.identifier.thread_id,
                turnId="compact-turn",
            )
        ))
        await compacting
        await subscription.wait_until_idle()
        assert not any(
            isinstance(event, MessageSent) and event.message == "continue"
            for event in events
        )
        pipeline.unsubscribe(subscription)
        await agent.close()

    asyncio.run(scenario())


def test_close_cancels_in_progress_compaction(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        client = FakeAsyncCodexClient()
        agent = CodexAgent(configuration(tmp_path), pipeline, client)
        await agent.new_session("Conversation 1")
        compacting = asyncio.create_task(agent.compact())
        await asyncio.sleep(0)

        await agent.close()

        with pytest.raises(asyncio.CancelledError):
            await compacting
        assert agent.status() is AgentStatus.INACTIVE
        assert client.closed == 1

    asyncio.run(scenario())


def test_compaction_failure_restores_idle_and_publishes_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        client = FakeAsyncCodexClient()
        client.compact_error = RuntimeError("compact failed")
        agent = CodexAgent(configuration(tmp_path), pipeline, client)
        await agent.new_session("Conversation 1")
        error_waiter = asyncio.create_task(pipeline.listen(AgentError))
        await asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="compact failed"):
            await agent.compact()

        assert agent.status() is AgentStatus.IDLE
        assert (await error_waiter).message == "compact failed"
        await agent.close()

    asyncio.run(scenario())


def test_compaction_timeout_restores_idle_and_releases_route(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        client = FakeAsyncCodexClient()
        agent = CodexAgent(configuration(tmp_path), pipeline, client)
        agent._COMPACTION_TIMEOUT_SECONDS = 0.01
        await agent.new_session("Conversation 1")

        with pytest.raises(RuntimeError, match="Compaction timed out"):
            await agent.compact()

        assert agent.status() is AgentStatus.IDLE
        assert client.compaction_operations == []
        await agent.close()

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


def test_non_ui_and_unknown_codex_notifications_have_distinct_contracts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        agent = CodexAgent(configuration(tmp_path), EventPipeline(), FakeAsyncCodexClient())
        identifier = CodexSessionIdentifier(thread_id="thread-1")
        known_event = await agent._translate_notification(
            identifier,
            SimpleNamespace(payload=FileChangeOutputDeltaNotification(
                delta="changed", itemId="item-1", threadId="thread-1", turnId="turn-1"
            )),
        )
        unknown_event = await agent._translate_notification(
            identifier, SimpleNamespace(payload=SimpleNamespace())
        )

        assert isinstance(known_event, ClientRecognizedProviderNoOp)
        assert known_event.category == "file_change_progress"
        assert isinstance(unknown_event, ClientUnknownProviderEvent)
        assert unknown_event.description == "Unknown Codex notification: SimpleNamespace"
        await agent.close()

    asyncio.run(scenario())


def test_codex_drops_command_and_mcp_progress_at_provider_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        agent = CodexAgent(configuration(tmp_path), EventPipeline(), FakeAsyncCodexClient())
        identifier = CodexSessionIdentifier(thread_id="thread-1")
        command = await agent._translate_notification(
            identifier,
            SimpleNamespace(payload=CommandExecutionOutputDeltaNotification(
                delta="partial", itemId="command-1", threadId="thread-1", turnId="turn-1"
            )),
        )
        mcp = await agent._translate_notification(
            identifier,
            SimpleNamespace(payload=McpToolCallProgressNotification(
                itemId="call-1", message="partial", threadId="thread-1", turnId="turn-1"
            )),
        )
        assert command is None
        assert mcp is None
        await agent.close()

    asyncio.run(scenario())


def test_codex_drops_command_and_mcp_starts_at_provider_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        agent = CodexAgent(configuration(tmp_path), EventPipeline(), FakeAsyncCodexClient())
        identifier = CodexSessionIdentifier(thread_id="thread-1")
        command = CommandExecutionThreadItem(
            id="command-1", type="commandExecution", command="pytest", cwd="C:/work",
            status=CommandExecutionStatus.in_progress, commandActions=[], exitCode=None,
        )
        mcp = McpToolCallThreadItem(
            id="call-1", type="mcpToolCall", server="tools", tool="inspect",
            arguments={}, status=McpToolCallStatus.in_progress,
        )
        for item in (command, mcp):
            event = await agent._translate_notification(
                identifier,
                SimpleNamespace(payload=ItemStartedNotification(
                    item=item, startedAtMs=0, threadId="thread-1", turnId="turn-1"
                )),
            )
            assert event is None
        await agent.close()

    asyncio.run(scenario())


def test_codex_terminal_command_event_retains_complete_provider_output(tmp_path: Path) -> None:
    async def scenario() -> None:
        agent = CodexAgent(configuration(tmp_path), EventPipeline(), FakeAsyncCodexClient())
        output = "H" * 40 + "discard" * 20 + "T" * 40
        event = agent._completed_activity(CommandExecutionThreadItem(
            id="command-1", type="commandExecution", command="pytest", cwd="C:/work",
            status=CommandExecutionStatus.completed, commandActions=[], exitCode=0,
            aggregatedOutput=output,
        ))
        assert isinstance(event, ClientCommandExecutionCompleted)
        assert event.output == output
        await agent.close()

    asyncio.run(scenario())
