"""Durable lifecycle contracts for live provider activity transcript cards."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from pairo.core.agent import AgentConfiguration, SandboxPermission
from pairo.core.clients import FakeAgent
from pairo.core.events import (
    AgentCommandExecutionCompleted,
    AgentCommandExecutionProgress,
    AgentMcpToolCallCompleted,
    AgentMcpToolCallProgress,
    AgentError,
    AgentResponseInterrupted,
    EventPipeline,
)
from pairo.ui.activity_terminalization import ActivityTerminalOutcome, PendingActivityTerminalizer
from pairo.ui.app import AgentChatApp
from pairo.ui.models import EventDetailLevel, TranscriptEntry
from pairo.ui.sqlite_session_repository import SqliteSessionRepository
from pairo.ui.transcript_events import TranscriptEntryAdded, TranscriptEntryUpdated
from pairo.ui.transcript_service import TranscriptService
from pairo.ui.transcribers import default_transcribers
from pairo.ui.transcribers.text_transcript import SourceHeading, SourcedTranscript


def test_progress_transcribers_are_registered_and_hide_provider_id() -> None:
    registry = default_transcribers()
    agent_id = uuid4()
    for event in (
        AgentCommandExecutionProgress(agent_id=agent_id, session_name="main", activity_id="provider-secret", output_delta="first"),
        AgentMcpToolCallProgress(agent_id=agent_id, session_name="main", activity_id="provider-secret", message="calling"),
    ):
        routed = registry.event_transcriber_for(event).transcribe(event)
        assert "provider-secret" not in routed.entry.content
        assert "provider-secret" not in str(routed.entry.metadata)
        assert routed.correlated_entry_id == routed.entry.entry_id


def test_empty_progress_text_uses_safe_sourced_fallbacks() -> None:
    registry = default_transcribers()
    agent_id = uuid4()
    cases = (
        (AgentCommandExecutionProgress(agent_id=agent_id, session_name="main", activity_id="provider-secret", output_delta=""), "No output yet."),
        (AgentMcpToolCallProgress(agent_id=agent_id, session_name="main", activity_id="provider-secret", message=""), "Working…"),
    )
    for event, fallback in cases:
        transcriber = registry.event_transcriber_for(event)
        routed = transcriber.transcribe(event)
        assert routed.entry.role == "agent"
        assert fallback in routed.entry.content
        assert "provider-secret" not in routed.entry.content
        assert routed.correlated_entry_id == routed.entry.entry_id
        renderer = registry.transcript_transcriber_for(routed.entry.metadata["type"])
        assert renderer is not None
        assert isinstance(renderer.render_transcript(routed.entry, None), SourcedTranscript)  # type: ignore[arg-type]


def test_sqlite_progress_lifecycle_is_isolated_and_survives_reload(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository(tmp_path)
        pipeline = EventPipeline()
        service = TranscriptService(repository, pipeline)
        added: list[TranscriptEntryAdded] = []
        updated: list[TranscriptEntryUpdated] = []
        add_subscription = pipeline.subscribe(TranscriptEntryAdded, lambda event, _: added.append(event))
        update_subscription = pipeline.subscribe(TranscriptEntryUpdated, lambda event, _: updated.append(event))
        registry = default_transcribers()
        first_agent, second_agent = uuid4(), uuid4()
        for agent, session in ((first_agent, "main"), (first_agent, "other"), (second_agent, "main")):
            await repository.save_session(agent, session, {"id": session})

        async def persist(event: object) -> None:
            routed = registry.event_transcriber_for(event).transcribe(event)  # type: ignore[arg-type]
            await service.upsert(routed.agent_id, routed.session_name, routed.entry)

        await persist(AgentCommandExecutionProgress(agent_id=first_agent, session_name="main", activity_id="same", output_delta="old"))
        await persist(AgentCommandExecutionProgress(agent_id=first_agent, session_name="main", activity_id="same", output_delta="latest"))
        await persist(AgentMcpToolCallProgress(agent_id=first_agent, session_name="main", activity_id="same", message="tool progress"))
        await persist(AgentCommandExecutionProgress(agent_id=first_agent, session_name="other", activity_id="same", output_delta="other"))
        await persist(AgentCommandExecutionProgress(agent_id=second_agent, session_name="main", activity_id="same", output_delta="agent two"))
        await persist(AgentCommandExecutionCompleted(agent_id=first_agent, session_name="main", activity_id="same", command="echo ok", status="completed", output="all output"))
        await persist(AgentMcpToolCallCompleted(agent_id=first_agent, session_name="main", activity_id="completion-only", server="tools", tool="inspect", arguments={}, status="completed"))
        await add_subscription.wait_until_idle()
        await update_subscription.wait_until_idle()

        entries = await repository.entries_for(first_agent, "main")
        assert len(entries) == 3
        command = next(entry for entry in entries if entry.metadata["type"] == "command_execution")
        assert "all output" in command.content and "latest" not in command.content
        assert all("same" not in str(entry.metadata) and "same" not in entry.content for entry in entries)
        assert len(added) == 5 and len(updated) == 2
        assert len(await repository.entries_for(first_agent, "other")) == 1
        assert len(await repository.entries_for(second_agent, "main")) == 1
        await repository.close()

        reopened = SqliteSessionRepository(tmp_path)
        reloaded = await reopened.entries_for(first_agent, "main")
        assert len(reloaded) == 3
        assert any(entry.metadata["type"] == "command_execution" for entry in reloaded)
        await reopened.close()
    asyncio.run(scenario())


def test_completion_only_adds_and_failed_upsert_does_not_publish() -> None:
    class FailingRepository(SqliteSessionRepository):
        async def upsert_entry(self, *args: object) -> bool:
            raise OSError("disk unavailable")

    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = FailingRepository()
        service = TranscriptService(repository, pipeline)
        events: list[object] = []
        subscription = pipeline.subscribe(TranscriptEntryAdded, lambda event, _: events.append(event))
        entry = TranscriptEntry("agent", "in progress", EventDetailLevel.NORMAL, {"type": "command_execution_progress"})
        with pytest.raises(OSError, match="disk unavailable"):
            await service.upsert(uuid4(), "main", entry)
        await subscription.wait_until_idle()
        assert events == []
    asyncio.run(scenario())


def test_ui_replaces_live_card_with_completed_card(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = FakeAgent(AgentConfiguration(name="reviewer", sandbox=SandboxPermission.READ_ONLY, workspace=tmp_path, model="test"), pipeline, lambda: "approved")
        active = await agent.new_session("main")
        agent_id = agent.configuration().id
        await repository.save_session(agent_id, active.name, active.identifier.model_dump(mode="json"))
        app = AgentChatApp({agent_id: agent}, pipeline, repository)
        async with app.run_test() as pilot:
            pipeline.push(AgentCommandExecutionProgress(agent_id=agent_id, session_name="main", activity_id="secret", output_delta="one"))
            pipeline.push(AgentCommandExecutionProgress(agent_id=agent_id, session_name="main", activity_id="secret", output_delta="two"))
            for _ in range(4): await pilot.pause()
            assert len(app.chats[agent_id].transcript.entries) == 1
            assert any(value in app.chats[agent_id].transcript.entries[0].content for value in ("one", "two"))
            pipeline.push(AgentCommandExecutionCompleted(agent_id=agent_id, session_name="main", activity_id="secret", command="echo", status="completed", output="done"))
            for _ in range(4): await pilot.pause()
            entry = app.chats[agent_id].transcript.entries[0]
            assert entry.metadata["type"] == "command_execution" and "done" in entry.content
        await agent.close(); await repository.close()
    asyncio.run(scenario())


def test_terminalization_is_isolated_idempotent_and_survives_reload(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository(tmp_path)
        pipeline = EventPipeline()
        service = TranscriptService(repository, pipeline)
        registry = default_transcribers()
        first_agent, second_agent = uuid4(), uuid4()
        for agent, session in ((first_agent, "main"), (first_agent, "other"), (second_agent, "main")):
            await repository.save_session(agent, session, {"id": session})

        async def persist(event: object) -> None:
            routed = registry.event_transcriber_for(event).transcribe(event)  # type: ignore[arg-type]
            await service.upsert(routed.agent_id, routed.session_name, routed.entry)

        await persist(AgentCommandExecutionProgress(agent_id=first_agent, session_name="main", activity_id="command", output_delta="latest command"))
        await persist(AgentMcpToolCallProgress(agent_id=first_agent, session_name="main", activity_id="tool", message="latest tool"))
        await persist(AgentCommandExecutionCompleted(agent_id=first_agent, session_name="main", activity_id="complete", command="echo ok", status="completed", output="done"))
        await persist(AgentCommandExecutionProgress(agent_id=first_agent, session_name="other", activity_id="other", output_delta="other"))
        await persist(AgentMcpToolCallProgress(agent_id=second_agent, session_name="main", activity_id="agent-two", message="agent two"))
        non_activity = TranscriptEntry("system", "leave me", EventDetailLevel.NORMAL, {"type": "system"})
        await service.add(first_agent, "main", non_activity)

        updated: list[TranscriptEntryUpdated] = []
        subscription = pipeline.subscribe(TranscriptEntryUpdated, lambda event, _: updated.append(event))
        await service.terminalize_pending_activities(first_agent, "main", ActivityTerminalOutcome.INTERRUPTED)
        await service.terminalize_pending_activities(first_agent, "main", ActivityTerminalOutcome.INTERRUPTED)
        await subscription.wait_until_idle()
        entries = await repository.entries_for(first_agent, "main")
        assert [entry.metadata["type"] for entry in entries] == ["command_execution", "mcp_tool_call", "command_execution", "system"]
        assert [entry.entry_id for entry in entries] == [event.entry.entry_id for event in updated] + [entries[2].entry_id, entries[3].entry_id]
        assert "Interrupted" in entries[0].content and "latest command" in entries[0].content
        assert "Interrupted" in entries[1].content and "latest tool" in entries[1].content
        assert "Completed successfully" in entries[2].content and entries[3] == non_activity
        assert len(updated) == 2
        assert (await repository.entries_for(first_agent, "other"))[0].metadata["type"] == "command_execution_progress"
        assert (await repository.entries_for(second_agent, "main"))[0].metadata["type"] == "mcp_tool_call_progress"
        await repository.close()

        reopened = SqliteSessionRepository(tmp_path)
        reloaded = await reopened.entries_for(first_agent, "main")
        assert [entry.entry_id for entry in reloaded] == [entry.entry_id for entry in entries]
        assert "Interrupted" in reloaded[0].content and "latest tool" in reloaded[1].content
        await reopened.close()
    asyncio.run(scenario())


def test_terminalization_rolls_back_and_does_not_notify_when_persistence_fails() -> None:
    class FailingTerminalizer(PendingActivityTerminalizer):
        def terminalize(self, entry: TranscriptEntry[object]) -> TranscriptEntry[object]:
            raise OSError("cannot write terminal state")

    class FailingRepository(SqliteSessionRepository):
        async def terminalize_pending_activities(self, *args: object) -> tuple[TranscriptEntry[object], ...]:
            raise OSError("disk unavailable")

    async def scenario() -> None:
        repository = SqliteSessionRepository()
        agent_id = uuid4()
        await repository.save_session(agent_id, "main", {})
        pending = TranscriptEntry("agent", "Command — In progress\n\nLatest output:\nlatest", EventDetailLevel.NORMAL, {"type": "command_execution_progress"})
        await repository.add_entry(agent_id, "main", pending)
        with pytest.raises(OSError, match="cannot write"):
            await repository.terminalize_pending_activities(agent_id, "main", FailingTerminalizer(ActivityTerminalOutcome.ERROR))
        assert (await repository.entries_for(agent_id, "main"))[0] == pending
        await repository.close()

        pipeline = EventPipeline()
        failed_service = TranscriptService(FailingRepository(), pipeline)
        updates: list[TranscriptEntryUpdated] = []
        subscription = pipeline.subscribe(TranscriptEntryUpdated, lambda event, _: updates.append(event))
        with pytest.raises(OSError, match="disk unavailable"):
            await failed_service.terminalize_pending_activities(uuid4(), "main", ActivityTerminalOutcome.ERROR)
        await subscription.wait_until_idle()
        assert updates == []
    asyncio.run(scenario())


def test_ui_terminal_events_order_error_after_cards_and_hide_provider_ids(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = FakeAgent(AgentConfiguration(name="reviewer", sandbox=SandboxPermission.READ_ONLY, workspace=tmp_path, model="test"), pipeline, lambda: "approved")
        active = await agent.new_session("main")
        agent_id = agent.configuration().id
        await repository.save_session(agent_id, active.name, active.identifier.model_dump(mode="json"))
        app = AgentChatApp({agent_id: agent}, pipeline, repository)
        async with app.run_test() as pilot:
            pipeline.push(AgentCommandExecutionProgress(agent_id=agent_id, session_name="main", activity_id="private-command", output_delta="last command"))
            pipeline.push(AgentMcpToolCallProgress(agent_id=agent_id, session_name="main", activity_id="private-tool", message="last tool"))
            for _ in range(4): await pilot.pause()
            pipeline.push(AgentError(agent_id=agent_id, session_name="main", message="network failed"))
            for _ in range(6): await pilot.pause()
            entries = app.chats[agent_id].transcript.entries
            assert [entry.metadata["type"] for entry in entries] == ["command_execution", "mcp_tool_call", "error"]
            assert all("Stopped after error" in entry.content for entry in entries[:2])
            assert entries[-1].content == "Agent error: network failed"
            assert all("private-" not in entry.content for entry in entries)
            pipeline.push(AgentMcpToolCallProgress(agent_id=agent_id, session_name="main", activity_id="private-interrupt", message="interrupt detail"))
            for _ in range(4): await pilot.pause()
            pipeline.push(AgentResponseInterrupted(agent_id=agent_id, session_name="main"))
            for _ in range(4): await pilot.pause()
            entries = app.chats[agent_id].transcript.entries
            assert len(entries) == 4
            assert entries[-1].metadata["type"] == "mcp_tool_call"
            assert "Interrupted" in entries[-1].content and "interrupt detail" in entries[-1].content
            rendered = app.chats[agent_id].transcript.query(SourcedTranscript)
            assert rendered
            headings = [heading.render().plain for heading in app.chats[agent_id].transcript.query(SourceHeading)]
            assert headings.count("reviewer") == 3 and all("private" not in heading for heading in headings)
        await agent.close(); await repository.close()
    asyncio.run(scenario())




def test_app_teardown_flushes_coalesced_progress(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        repository = SqliteSessionRepository()
        agent = FakeAgent(AgentConfiguration(name="reviewer", sandbox=SandboxPermission.READ_ONLY, workspace=tmp_path, model="test"), pipeline, lambda: "approved")
        active = await agent.new_session("main")
        agent_id = agent.configuration().id
        await repository.save_session(agent_id, active.name, active.identifier.model_dump(mode="json"))
        app = AgentChatApp({agent_id: agent}, pipeline, repository)
        async with app.run_test() as pilot:
            pipeline.push(AgentCommandExecutionProgress(agent_id=agent_id, session_name="main", activity_id="secret", output_delta="first"))
            pipeline.push(AgentCommandExecutionProgress(agent_id=agent_id, session_name="main", activity_id="secret", output_delta="latest"))
            for _ in range(2):
                await pilot.pause()
        entries = await repository.entries_for(agent_id, "main")
        assert len(entries) == 1 and "latest" in entries[0].content
        await agent.close()
        await repository.close()
    asyncio.run(scenario())

