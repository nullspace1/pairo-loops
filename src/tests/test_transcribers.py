"""Tests for combined transcript conversion and rendering."""

from uuid import uuid4

import pytest
from textual.widgets import Collapsible

from pairo.core.events import (
    AgentActivityCompleted,
    AgentError,
    AgentTokenUsageUpdated,
    MessageSent,
    TokenUsage,
)
from pairo.ui.transcribers import (
    ActivityCompletedTranscriber,
    TranscriberRegistry,
    MessageSentTranscriber,
    TokenUsageUpdatedTranscriber,
    default_transcribers,
)
from pairo.ui.transcribers.session_table_transcriber import SessionTableTranscriber
from pairo.ui.models import EventDetailLevel, TranscriptEntry
from pairo.ui.presentation import CompletedActivityPresenter


AGENT_ID = uuid4()


def test_default_transcribers_register_concrete_event_and_transcript_types() -> None:
    registry = default_transcribers()

    assert AgentActivityCompleted in registry.event_types
    assert AgentTokenUsageUpdated in registry.event_types
    for transcript_type in (
        "activity",
        "error",
        "message",
        "network",
        "permission_request",
        "token_usage",
    ):
        assert registry.transcript_transcriber_for(transcript_type) is not None


def test_registry_routes_events_and_finds_transcript_renderers() -> None:
    registry = TranscriberRegistry()
    registry.register(MessageSentTranscriber())
    event = MessageSent(
        agent_id=AGENT_ID,
        session_name="Conversation 1",
        message="Hello",
    )

    routed = registry.event_transcriber_for(event).transcribe(event)

    assert routed.entry.metadata == {"type": "message"}
    assert registry.transcript_transcriber_for("message") is not None
    assert registry.transcript_transcriber_for("missing") is None


def test_registry_rejects_duplicate_event_registration() -> None:
    registry = TranscriberRegistry()
    registry.register(MessageSentTranscriber())

    with pytest.raises(ValueError, match="Multiple transcribers registered for event MessageSent"):
        registry.register(MessageSentTranscriber())


def test_transcriber_rejects_an_event_other_than_its_declared_type() -> None:
    transcriber = MessageSentTranscriber()

    with pytest.raises(TypeError, match="MessageSentTranscriber requires MessageSent"):
        transcriber.write(
            AgentError(
                agent_id=AGENT_ID,
                session_name="Conversation 1",
                message="failure",
            )
        )


def test_completed_mcp_calls_and_commands_are_visible_in_normal_mode() -> None:
    transcriber = ActivityCompletedTranscriber()

    command = transcriber.write(
        AgentActivityCompleted(
            agent_id=AGENT_ID,
            session_name="Conversation 1",
            activity={"type": "commandExecution", "command": "pytest"},
        )
    )
    mcp_call = transcriber.write(
        AgentActivityCompleted(
            agent_id=AGENT_ID,
            session_name="Conversation 1",
            activity={"type": "mcpToolCall", "serverName": "docs", "tool": "search"},
        )
    )

    assert command is not None
    assert command.entry.detail_level is EventDetailLevel.NORMAL
    assert mcp_call is not None
    assert mcp_call.entry.detail_level is EventDetailLevel.NORMAL


def test_completed_action_renders_collapsed_details() -> None:
    transcriber = ActivityCompletedTranscriber()
    routed = transcriber.write(
        AgentActivityCompleted(
            agent_id=AGENT_ID,
            session_name="Conversation 1",
            activity={"type": "commandExecution", "command": "pytest"},
        )
    )
    widget = transcriber.render(routed.entry, None)  # type: ignore[arg-type]

    assert isinstance(widget, Collapsible)
    assert widget.collapsed
    assert widget.title == "Command completed"


def test_session_table_transcriber_has_table_and_empty_state() -> None:
    transcriber = SessionTableTranscriber()
    entry = TranscriptEntry(
        "system", "Sessions", EventDetailLevel.NORMAL,
        {"type": "session_table", "sessions": []},
    )
    widget = transcriber.render_transcript(entry, None)  # type: ignore[arg-type]
    assert widget._sessions == ()


def test_session_table_transcriber_marks_current_session_and_rows() -> None:
    entry = TranscriptEntry(
        "system", "Sessions", EventDetailLevel.NORMAL,
        {"type": "session_table", "sessions": [{"session_name": "Current", "current": True, "created_at": "2026-01-01T00:00:00+00:00", "last_message_at": None, "last_updated_at": "2026-01-01T01:00:00+00:00"}]},
    )
    widget = SessionTableTranscriber().render_transcript(entry, None)  # type: ignore[arg-type]
    assert widget._sessions[0]["session_name"] == "Current"
    assert widget._sessions[0]["current"]


def test_other_completed_activity_remains_debug_only() -> None:
    formatted = ActivityCompletedTranscriber().write(
        AgentActivityCompleted(
            agent_id=AGENT_ID,
            session_name="Conversation 1",
            activity={"type": "fileChange", "path": "src/example.py"},
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


def test_token_usage_is_written_as_a_session_log_entry() -> None:
    formatted = TokenUsageUpdatedTranscriber().write(
        AgentTokenUsageUpdated(
            agent_id=AGENT_ID,
            session_name="Conversation 1",
            last=TokenUsage(
                input_tokens=100,
                cached_input_tokens=40,
                output_tokens=25,
                reasoning_output_tokens=10,
                total_tokens=125,
            ),
            total=TokenUsage(
                input_tokens=300,
                cached_input_tokens=80,
                output_tokens=75,
                reasoning_output_tokens=30,
                total_tokens=375,
            ),
            model_context_window=200_000,
        )
    )

    assert formatted is not None
    assert formatted.entry.metadata == {"type": "token_usage"}
    assert "turn: 100 input (40 cached)" in formatted.entry.content
    assert "session: 375 total" in formatted.entry.content
