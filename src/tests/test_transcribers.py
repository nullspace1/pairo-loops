from uuid import uuid4

import pytest

from pairo.core.events import (
    AgentCommandExecutionCompleted,
    AgentError,
    AgentMcpToolCallCompleted,
    AgentTokenUsageUpdated,
    MessageSent,
    TokenUsage,
)
from pairo.ui.models import EventDetailLevel, TranscriptEntry
from pairo.ui.transcribers import (
    MessageSentTranscriber,
    TranscriberRegistry,
    TokenUsageUpdatedTranscriber,
    default_transcribers,
)
from pairo.ui.transcribers.session_table_transcriber import SessionTableTranscriber


AGENT_ID = uuid4()


def test_default_transcribers_register_concrete_event_and_transcript_types() -> None:
    registry = default_transcribers()

    assert AgentCommandExecutionCompleted in registry.event_types
    assert AgentMcpToolCallCompleted in registry.event_types
    assert AgentTokenUsageUpdated in registry.event_types
    for transcript_type in (
        "command_execution",
        "mcp_tool_call",
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


def test_session_table_transcriber_has_table_and_empty_state() -> None:
    transcriber = SessionTableTranscriber()
    entry = TranscriptEntry(
        "system", "Sessions", EventDetailLevel.NORMAL,
        {"type": "session_table", "sessions": []},
    )
    widget = transcriber.render_transcript(entry, None)  # type: ignore[arg-type]
    assert widget._content._sessions == ()


def test_session_table_transcriber_marks_current_session_and_rows() -> None:
    entry = TranscriptEntry(
        "system", "Sessions", EventDetailLevel.NORMAL,
        {"type": "session_table", "sessions": [{"session_name": "Current", "current": True, "created_at": "2026-01-01T00:00:00+00:00", "last_message_at": None, "last_updated_at": "2026-01-01T01:00:00+00:00"}]},
    )
    widget = SessionTableTranscriber().render_transcript(entry, None)  # type: ignore[arg-type]
    assert widget._content._sessions[0]["session_name"] == "Current"
    assert widget._content._sessions[0]["current"]


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

    assert formatted.entry.metadata == {"type": "token_usage"}
    assert "turn: 100 input (40 cached)" in formatted.entry.content
    assert "session: 375 total" in formatted.entry.content
