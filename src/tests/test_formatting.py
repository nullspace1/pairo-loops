"""Tests for transcript formatting visibility."""

from pairo.core.events import AgentEvent, ClientActivityCompleted
from pairo.ui.formatting import CompletedEventFormatter
from pairo.ui.models import EventDetailLevel
from pairo.ui.presentation import CompletedActivityPresenter


def test_completed_mcp_calls_and_commands_are_visible_in_normal_mode() -> None:
    formatter = CompletedEventFormatter()

    command = formatter.format(
        AgentEvent(
            agent_name="reviewer",
            session_name="Conversation 1",
            payload=ClientActivityCompleted(
                activity={"type": "commandExecution", "command": "pytest"}
            ),
        )
    )
    mcp_call = formatter.format(
        AgentEvent(
            agent_name="reviewer",
            session_name="Conversation 1",
            payload=ClientActivityCompleted(
                activity={"type": "mcpToolCall", "serverName": "docs", "tool": "search"}
            ),
        )
    )

    assert command is not None
    assert command.entry.detail_level is EventDetailLevel.NORMAL
    assert mcp_call is not None
    assert mcp_call.entry.detail_level is EventDetailLevel.NORMAL


def test_other_completed_activity_remains_debug_only() -> None:
    formatted = CompletedEventFormatter().format(
        AgentEvent(
            agent_name="reviewer",
            session_name="Conversation 1",
            payload=ClientActivityCompleted(
                activity={"type": "fileChange", "path": "src/example.py"}
            ),
        )
    )

    assert formatted is not None
    assert formatted.entry.detail_level is EventDetailLevel.DEBUG


def test_command_activity_uses_an_ordered_readable_block() -> None:
    presentation = CompletedActivityPresenter().present(
        {
            "type": "commandExecution",
            "command": "pytest\npython -m build",
            "cwd": "C:/project",
            "status": "completed",
            "exitCode": 0,
            "aggregatedOutput": "tests passed\npackage built",
            "threadId": "internal-thread",
        }
    )

    assert presentation.title == "Command completed"
    assert presentation.lines == (
        "Command: pytest\npython -m build",
        "Working directory: C:/project",
        "Status: completed",
        "Exit code: 0",
        "Output: tests passed\npackage built",
    )
    assert presentation.text == "Command completed\n\n" + "\n".join(presentation.lines)
    assert "internal-thread" not in presentation.text


def test_mcp_activity_formats_json_and_nested_result_without_raw_repr() -> None:
    presentation = CompletedActivityPresenter().present(
        {
            "type": "mcpToolCall",
            "serverName": "docs",
            "tool": "search",
            "arguments": '{"query":"agent loops","filters":["python","async"]}',
            "status": "completed",
            "result": {"matches": [{"title": "Guide", "id": "hidden-item"}]},
            "requestId": "hidden-request",
        }
    )

    assert presentation.title == "MCP tool call completed"
    assert presentation.lines == (
        "Server: docs",
        "Tool: search",
        "Arguments:",
        "  Query: agent loops",
        "  Filters:",
        "    - python",
        "    - async",
        "Status: completed",
        "Result:",
        "  Matches:",
        "    - Title: Guide",
    )
    assert "{'" not in presentation.text
    assert "hidden-" not in presentation.text


def test_activity_presenter_has_safe_fallbacks_for_missing_and_unknown_items() -> None:
    presenter = CompletedActivityPresenter()

    command = presenter.present({"type": "commandExecution", "id": "hidden"})
    unknown = presenter.present({"type": "customOperation", "payload": ["one", "two"]})

    assert command.lines == ("No additional details were provided.",)
    assert "hidden" not in command.text
    assert unknown.title == "Custom operation completed"
    assert unknown.lines == ("Payload:", "  - one", "  - two")
