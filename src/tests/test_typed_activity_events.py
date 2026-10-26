"""Focused contracts for typed routed activity events and summaries."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from pairo.core.events import (
    AgentActivityCompleted,
    AgentCommandExecutionCompleted,
    AgentMcpToolCallCompleted,
    ClientCommandExecutionCompleted,
    ClientMcpToolCallCompleted,
)
from pairo.ui.transcribers import (
    CommandExecutionTranscriber,
    McpToolCallTranscriber,
    default_transcribers,
)


AGENT_ID = uuid4()


def test_typed_activity_routes_to_concrete_subclass() -> None:
    routed = ClientCommandExecutionCompleted(
        activity_id="private", command="python -m pytest", status="completed", output="ok"
    ).to_routed_event(AGENT_ID, "main")
    assert isinstance(routed, AgentActivityCompleted)
    assert routed.command == "python -m pytest"
    assert not hasattr(routed, "activity")


@pytest.mark.parametrize(
    ("event_class", "fields", "invalid_status"),
    [
        (ClientCommandExecutionCompleted, {"activity_id": "command", "command": "pytest"}, "cancelled"),
        (ClientMcpToolCallCompleted, {"activity_id": "call", "server": "tools", "tool": "inspect", "arguments": {}}, "declined"),
    ],
)
def test_completion_status_contract_rejects_unsupported_values(
    event_class: type[ClientCommandExecutionCompleted] | type[ClientMcpToolCallCompleted],
    fields: dict[str, object],
    invalid_status: str,
) -> None:
    with pytest.raises(ValidationError):
        event_class(status=invalid_status, **fields)


def test_default_registry_uses_separate_activity_renderers() -> None:
    registry = default_transcribers()
    assert AgentCommandExecutionCompleted in registry.event_types
    assert registry.transcript_transcriber_for("command_execution") is not None
    assert registry.transcript_transcriber_for("mcp_tool_call") is not None


@pytest.mark.parametrize(
    ("status", "exit_code", "expected_outcome"),
    [
        ("completed", 0, "Completed successfully (exit code 0)"),
        ("failed", 1, "Failed (exit code 1)"),
        ("declined", None, "Declined"),
    ],
)
def test_command_summary_renders_human_outcome_and_optional_details(
    status: str, exit_code: int | None, expected_outcome: str
) -> None:
    event = AgentCommandExecutionCompleted(
        agent_id=AGENT_ID, session_name="main", activity_id="command", command="pytest -q",
        status=status, exit_code=exit_code,
    )
    entry = CommandExecutionTranscriber().write(event).entry

    assert entry.content.startswith("Command")
    assert expected_outcome in entry.content
    assert "Working directory: Not provided" in entry.content
    assert "No output was provided." in entry.content
    assert "CommandExecutionStatus" not in entry.content


def test_mcp_summary_renders_failure_and_preserves_optional_error_data() -> None:
    event = AgentMcpToolCallCompleted(
        agent_id=AGENT_ID, session_name="main", activity_id="call", server="tools",
        tool="inspect", arguments={"path": "README.md"}, status="failed", error="Not found",
    )
    entry = McpToolCallTranscriber().write(event).entry

    assert entry.content.startswith("MCP tools / inspect")
    assert "Failed: Not found" in entry.content
    assert '"Arguments": {' in entry.content
    assert '"Result": null' in entry.content
    assert '"Error": "Not found"' in entry.content
    assert "McpToolCallStatus" not in entry.content


def test_mcp_summary_handles_absent_optional_values() -> None:
    event = AgentMcpToolCallCompleted(
        agent_id=AGENT_ID, session_name="main", activity_id="call", server="tools",
        tool="inspect", arguments=None, status="completed",
    )
    entry = McpToolCallTranscriber().write(event).entry

    assert entry.content.startswith("MCP tools / inspect")
    assert "Completed successfully" in entry.content
    assert '"Arguments": null' in entry.content
    assert '"Result": null' in entry.content
    assert '"Error": null' in entry.content
