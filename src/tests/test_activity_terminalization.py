"""Polymorphic terminalization contracts for persisted live activity cards."""

import pytest

from pairo.ui.activity_terminalization import (
    ActivityTerminalOutcome,
    CommandExecutionPendingTerminalizer,
    McpToolCallPendingTerminalizer,
    PendingActivityTerminalizer,
)
from pairo.ui.models import EventDetailLevel, TranscriptEntry


def test_registered_family_terminalizers_preserve_their_own_presentations() -> None:
    terminalizer = PendingActivityTerminalizer(
        ActivityTerminalOutcome.INTERRUPTED,
        (CommandExecutionPendingTerminalizer(), McpToolCallPendingTerminalizer()),
    )
    command = TranscriptEntry(
        "agent", "Command — In progress\n\nLatest output:\ncommand",
        EventDetailLevel.NORMAL, {"type": "command_execution_progress"},
    )
    mcp = TranscriptEntry(
        "agent", "MCP tool — In progress\n\nmcp",
        EventDetailLevel.NORMAL, {"type": "mcp_tool_call_progress"},
    )

    terminal_command = terminalizer.terminalize(command)
    terminal_mcp = terminalizer.terminalize(mcp)

    assert terminal_command.metadata == {"type": "command_execution"}
    assert terminal_command.content.startswith("Command — Interrupted")
    assert terminal_mcp.metadata == {"type": "mcp_tool_call"}
    assert terminal_mcp.content.startswith("MCP tool — Interrupted")


def test_terminalizer_rejects_an_unregistered_family_safely() -> None:
    terminalizer = PendingActivityTerminalizer(
        ActivityTerminalOutcome.ERROR,
        (CommandExecutionPendingTerminalizer(),),
    )
    unsupported = TranscriptEntry(
        "system", "unknown", EventDetailLevel.NORMAL, {"type": "system"},
    )

    with pytest.raises(ValueError, match="Cannot terminalize transcript type 'system'"):
        terminalizer.terminalize(unsupported)
