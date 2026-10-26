"""Focused contracts for typed routed activity events and summaries."""

import re
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
    TranscriberRegistry,
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


def test_command_transcriber_bounds_unicode_output_without_mutating_event() -> None:
    limit = 64
    output = "🧪" * 80 + " middle " + "終" * 80
    event = AgentCommandExecutionCompleted(
        agent_id=AGENT_ID, session_name="main", activity_id="command", command="pytest -q",
        status="completed", output=output,
    )

    entry = CommandExecutionTranscriber(limit).write(event).entry
    bounded_output = entry.content.rsplit("\n\nOutput:\n", maxsplit=1)[1]
    marker = re.search(r"\n… \[(\d+) characters omitted\] …\n", bounded_output)

    assert marker is not None
    assert len(bounded_output) <= limit
    assert bounded_output.startswith(output[:1])
    assert bounded_output.endswith(output[-1:])
    assert int(marker.group(1)) == len(output) - (
        len(bounded_output) - len(marker.group(0))
    )
    assert event.output == output


def test_command_transcriber_preserves_none_and_in_limit_output() -> None:
    transcriber = CommandExecutionTranscriber(64)
    no_output = AgentCommandExecutionCompleted(
        agent_id=AGENT_ID, session_name="main", activity_id="none", command="true",
        status="completed", output=None,
    )
    short_output = AgentCommandExecutionCompleted(
        agent_id=AGENT_ID, session_name="main", activity_id="short", command="echo",
        status="completed", output="café",
    )

    assert "No output was provided." in transcriber.write(no_output).entry.content
    assert transcriber.write(short_output).entry.content.endswith("Output:\ncafé")
    assert no_output.output is None
    assert short_output.output == "café"


def test_command_transcriber_uses_a_64_kib_default_limit() -> None:
    output = "H" * (64 * 1024 + 100) + "T"
    event = AgentCommandExecutionCompleted(
        agent_id=AGENT_ID, session_name="main", activity_id="default", command="pytest",
        status="completed", output=output,
    )

    bounded_output = CommandExecutionTranscriber().write(event).entry.content.rsplit(
        "\n\nOutput:\n", maxsplit=1
    )[1]

    assert len(bounded_output) <= 64 * 1024
    assert bounded_output.endswith("T")
    assert event.output == output


@pytest.mark.parametrize(
    "limit", [0, CommandExecutionTranscriber.MINIMUM_COMMAND_OUTPUT_LIMIT - 1]
)
def test_command_transcriber_rejects_limits_that_cannot_retain_a_complete_marker(
    limit: int,
) -> None:
    with pytest.raises(ValueError, match="head character"):
        CommandExecutionTranscriber(limit)


def test_command_transcriber_accepts_minimum_limit_and_factory_or_custom_configuration() -> None:
    minimum = CommandExecutionTranscriber.MINIMUM_COMMAND_OUTPUT_LIMIT
    event = AgentCommandExecutionCompleted(
        agent_id=AGENT_ID, session_name="main", activity_id="command", command="pytest",
        status="completed", output="H" + "discard" * 100 + "T",
    )
    registry = default_transcribers(command_output_limit=minimum)
    custom_registry = TranscriberRegistry()
    custom_registry.register(CommandExecutionTranscriber(minimum))

    for configured_registry in (registry, custom_registry):
        bounded_output = configured_registry.event_transcriber_for(event).transcribe(
            event
        ).entry.content.rsplit("\n\nOutput:\n", maxsplit=1)[1]
        assert len(bounded_output) <= minimum
        assert bounded_output.startswith("H")
        assert bounded_output.endswith("T")
        assert "characters omitted" in bounded_output


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
