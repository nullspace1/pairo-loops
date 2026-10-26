"""Focused coverage for terminal-only conversation logging."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

from pairo.core.events import (
    AgentCommandExecutionCompleted,
    AgentRecognizedProviderNoOp,
    ConversationEventLog,
    DiagnosticEventLogPolicy,
    EventPipeline,
    MessageSent,
)


AGENT_ID = UUID("00000000-0000-0000-0000-000000000001")


def test_log_records_completed_activity_without_progress_snapshots(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        output_path = tmp_path / "conversation.jsonl"
        log = ConversationEventLog(pipeline, output_path)
        completed = AgentCommandExecutionCompleted(
            agent_id=AGENT_ID,
            session_name="main",
            activity_id="command-1",
            command="git status",
            status="completed",
            exit_code=0,
            output="clean",
        )
        pipeline.push(completed)
        await log.flush()
        records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
        assert [record["event_type"] for record in records] == ["AgentCommandExecutionCompleted"]
        assert records[0]["payload"] == completed.model_dump(mode="json")
        await log.close()

    asyncio.run(scenario())


def test_diagnostic_log_keeps_terminal_events_and_summarizes_no_ops(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        output_path = tmp_path / "diagnostic.jsonl"
        log = ConversationEventLog(pipeline, output_path, DiagnosticEventLogPolicy())
        pipeline.push(MessageSent(agent_id=AGENT_ID, session_name="main", message="terminal"))
        pipeline.push(AgentRecognizedProviderNoOp(
            agent_id=AGENT_ID, session_name="main", category="file_change_progress"
        ))
        await log.close()
        records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
        assert [record["event_type"] for record in records] == [
            "MessageSent", "AgentRecognizedProviderNoOp", "RecognizedProviderNoOpSummary"
        ]

    asyncio.run(scenario())
