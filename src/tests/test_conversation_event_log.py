"""Focused coverage for the shared, future-only conversation event log."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

from pairo.core.events import (
    AgentActivityCompleted,
    AgentActivityProgress,
    AgentActivityStarted,
    AgentCommandExecutionCompleted,
    AgentCommandExecutionProgress,
    AgentError,
    ConversationEventLog,
    EventPipeline,
    MessageSent,
    NetworkMessageSent,
    PermissionRequest,
    PermissionResponse,
    SessionCreated,
    SessionDeleted,
    SessionUpdated,
    UserInput,
)
from pairo.tools.conversation_event_log import (
    CoalescingEventLogPolicy,
    DiagnosticEventLogPolicy,
)
from pairo.core.permissions import CommandExecutionApprovalRequest, PermissionDecision


AGENT_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")
EMOJI_MESSAGE = "hola ðŸŒ"


class _FlushGatedConversationEventLog(ConversationEventLog):
    """Pause close after draining so a test can overlap it with flush."""

    def __init__(self, pipeline: EventPipeline, output_path: Path) -> None:
        super().__init__(pipeline, output_path)
        self.ready_to_flush = asyncio.Event()
        self.allow_flush = asyncio.Event()

    async def _close_after_drain(self) -> None:
        await self._subscription.wait_until_idle()
        self._pipeline.unsubscribe(self._subscription)
        self.ready_to_flush.set()
        await self.allow_flush.wait()
        try:
            self._flush_file()
        finally:
            self._file.close()


def test_log_records_all_event_families_with_json_payloads(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        output_path = tmp_path / "nested" / "events" / "conversation.jsonl"
        pipeline.push(UserInput(message="before construction"))
        log = ConversationEventLog(pipeline, output_path)
        events = [
            UserInput(message=EMOJI_MESSAGE),
            MessageSent(
                agent_id=AGENT_ID,
                session_name="main",
                message="user message",
            ),
            SessionCreated(
                agent_id=AGENT_ID,
                session_name="main",
                identifier={"thread": "created"},
            ),
            SessionUpdated(
                agent_id=AGENT_ID,
                session_name="main",
                identifier={"thread": "updated"},
            ),
            SessionDeleted(
                agent_id=AGENT_ID,
                session_name="main",
                selected_session_name="replacement",
            ),
            AgentActivityStarted(
                agent_id=AGENT_ID,
                session_name="main",
                activity={"type": "command", "command": "git status"},
            ),
            AgentCommandExecutionProgress(
                agent_id=AGENT_ID,
                session_name="main",
                activity_id="command-1",
                output_delta="50%",
            ),
            AgentCommandExecutionCompleted(
                agent_id=AGENT_ID,
                session_name="main",
                activity_id="command-1",
                command="git status",
                status="completed",
                exit_code=0,
            ),
            AgentError(
                agent_id=AGENT_ID,
                session_name="main",
                message="network disconnected",
            ),
            PermissionRequest(
                request_id="request-1",
                agent_id=AGENT_ID,
                session_name="main",
                request=CommandExecutionApprovalRequest(
                    session_identifier={}, command="git status"
                ),
            ),
            PermissionResponse(
                request_id="request-1",
                agent_id=AGENT_ID,
                session_name="main",
                decision=PermissionDecision.ACCEPT,
            ),
            NetworkMessageSent(
                channel_name="coordination",
                sender_id=AGENT_ID,
                sender_name="planner",
                recipient_id=OTHER_AGENT_ID,
                session_name="main",
                message="complete",
            ),
        ]
        for event in events:
            pipeline.push(event)

        await log.flush()
        assert output_path.parent.is_dir()
        records = [
            json.loads(line)
            for line in output_path.read_text(encoding="utf-8").splitlines()
        ]
        assert [record["event_type"] for record in records] == [
            type(event).__name__ for event in events
        ]
        assert [record["payload"] for record in records] == [
            event.model_dump(mode="json") for event in events
        ]
        assert all(record["timestamp"].endswith("+00:00") for record in records)
        contents = output_path.read_text(encoding="utf-8")
        assert EMOJI_MESSAGE in contents
        assert "\\ud83c\\udf0d" not in contents

        await log.close()
        await log.close()
        pipeline.push(UserInput(message="after close"))
        await asyncio.sleep(0)
        assert len(output_path.read_text(encoding="utf-8").splitlines()) == len(events)

    asyncio.run(scenario())


def test_close_drains_events_that_were_queued_before_shutdown(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        output_path = tmp_path / "conversation.jsonl"
        log = ConversationEventLog(pipeline, output_path)
        pipeline.push(UserInput(message="first"))
        pipeline.push(UserInput(message="second"))

        await log.close()

        records = [
            json.loads(line)
            for line in output_path.read_text(encoding="utf-8").splitlines()
        ]
        assert [record["payload"]["message"] for record in records] == [
            "first",
            "second",
        ]

    asyncio.run(scenario())


def test_coalescing_policy_writes_latest_progress_before_completion(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        output_path = tmp_path / "conversation.jsonl"
        log = ConversationEventLog(
            pipeline, output_path, CoalescingEventLogPolicy()
        )
        pipeline.push(AgentCommandExecutionProgress(
            agent_id=AGENT_ID,
            session_name="main",
            activity_id="activity-1", output_delta="10",
        ))
        pipeline.push(AgentCommandExecutionProgress(
            agent_id=AGENT_ID,
            session_name="main",
            activity_id="activity-1", output_delta="90",
        ))
        pipeline.push(AgentCommandExecutionCompleted(
            agent_id=AGENT_ID,
            session_name="main",
            activity_id="activity-1", command="pytest", status="completed",
        ))

        await log.close()

        records = [
            json.loads(line)
            for line in output_path.read_text(encoding="utf-8").splitlines()
        ]
        assert [record["event_type"] for record in records] == [
            "AgentCommandExecutionProgress",
            "AgentCommandExecutionCompleted",
        ]
        assert records[0]["payload"]["output_delta"] == "90"

    asyncio.run(scenario())


def test_coalescing_policy_flushes_uncompleted_progress(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        output_path = tmp_path / "conversation.jsonl"
        log = ConversationEventLog(
            pipeline, output_path, CoalescingEventLogPolicy()
        )
        pipeline.push(AgentCommandExecutionProgress(
            agent_id=AGENT_ID,
            session_name="main",
            activity_id="activity-1", output_delta="50",
        ))

        await log.flush()

        record = json.loads(output_path.read_text(encoding="utf-8").strip())
        assert record["payload"]["output_delta"] == "50"
        await log.close()

    asyncio.run(scenario())


def test_diagnostic_policy_drops_text_deltas_and_samples_unsupported_events(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        from pairo.core.events import AgentTextDelta, AgentUnsupportedActivity

        pipeline = EventPipeline()
        output_path = tmp_path / "diagnostic.jsonl"
        log = ConversationEventLog(
            pipeline, output_path, DiagnosticEventLogPolicy()
        )
        pipeline.push(AgentTextDelta(
            agent_id=AGENT_ID, session_name="main", text="partial"
        ))
        for _ in range(3):
            pipeline.push(AgentUnsupportedActivity(
                agent_id=AGENT_ID,
                session_name="main",
                description="Unsupported notification",
            ))
        pipeline.push(MessageSent(
            agent_id=AGENT_ID, session_name="main", message="complete"
        ))

        await log.close()

        records = [
            json.loads(line)
            for line in output_path.read_text(encoding="utf-8").splitlines()
        ]
        assert [record["event_type"] for record in records] == [
            "AgentUnsupportedActivity",
            "MessageSent",
        ]

    asyncio.run(scenario())


def test_flush_waits_for_an_in_progress_close(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        output_path = tmp_path / "conversation.jsonl"
        log = _FlushGatedConversationEventLog(pipeline, output_path)
        pipeline.push(UserInput(message="queued before close"))

        close_task = asyncio.create_task(log.close())
        await log.ready_to_flush.wait()

        flush_task = asyncio.create_task(log.flush())
        await asyncio.sleep(0)
        assert not flush_task.done()

        log.allow_flush.set()
        await flush_task
        assert [
            json.loads(line)["payload"]["message"]
            for line in output_path.read_text(encoding="utf-8").splitlines()
        ] == ["queued before close"]
        await close_task

    asyncio.run(scenario())
