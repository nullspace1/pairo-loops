"""Lifecycle coverage for incremental transcript presentation."""

import asyncio
from uuid import UUID, uuid4

from textual.app import App, ComposeResult
from textual.widgets import Collapsible

from pairo.core.events import EventPipeline
from pairo.ui.models import EventDetailLevel, TranscriptEntry
from pairo.ui.transcribers import default_transcribers
from pairo.ui.widgets.transcript import Transcript, TranscriptEntryWidget


class TranscriptApp(App[None]):
    def __init__(self, transcript: Transcript) -> None:
        super().__init__()
        self.transcript = transcript

    def compose(self) -> ComposeResult:
        yield self.transcript


class CountingTranscript(Transcript):
    def __init__(self) -> None:
        super().__init__(uuid4(), "one", EventPipeline(), default_transcribers())
        self.clear_count = 0

    async def remove_children(self) -> list[object]:
        self.clear_count += 1
        return await super().remove_children()


def entry(content: str, detail: EventDetailLevel = EventDetailLevel.NORMAL) -> TranscriptEntry[dict[str, str]]:
    return TranscriptEntry("agent", content, detail, {"type": "message"})


def transcript() -> Transcript:
    return Transcript(uuid4(), "one", EventPipeline(), default_transcribers())


def widget_for(view: Transcript, entry_id: UUID) -> TranscriptEntryWidget:
    return next(widget for widget in view.query(TranscriptEntryWidget) if widget.entry_id == entry_id)


def test_incremental_changes_preserve_unrelated_widget_identity_and_detail_state() -> None:
    async def scenario() -> None:
        view = transcript()
        first = entry("first")
        debug = entry("debug", EventDetailLevel.DEBUG)
        second = entry("second")
        app = TranscriptApp(view)
        async with app.run_test() as pilot:
            await view.populate_entries((first, debug, second))
            first_widget = widget_for(view, first.entry_id)
            second_widget = widget_for(view, second.entry_id)
            assert not widget_for(view, debug.entry_id).display

            await view.add_entry(entry("later"))
            await view.update_entry(TranscriptEntry(
                "agent", "second changed", EventDetailLevel.NORMAL,
                {"type": "message"}, second.entry_id,
            ))
            await view.delete_entry(first.entry_id)
            await view.set_detail_level(EventDetailLevel.DEBUG)
            await pilot.pause()

            assert first_widget not in view.query(TranscriptEntryWidget)
            assert widget_for(view, second.entry_id) is second_widget
            assert widget_for(view, debug.entry_id).display
            assert [child.entry_id for child in view.query(TranscriptEntryWidget)] == [
                debug.entry_id, second.entry_id, view.entries[-1].entry_id,
            ]

    asyncio.run(scenario())


def test_entry_wrapper_replaces_only_its_renderer_and_preserves_card_state() -> None:
    async def scenario() -> None:
        view = transcript()
        progress = TranscriptEntry(
            "agent", "Command - In progress\n\nLatest output:\nbuilding",
            EventDetailLevel.NORMAL, {"type": "command_execution_progress"},
        )
        debug = entry("debug", EventDetailLevel.DEBUG)
        sibling = entry("sibling")
        completed = TranscriptEntry(
            "agent", "Command - Completed successfully\n\nOutput:\ndone",
            EventDetailLevel.NORMAL, {"type": "command_execution"},
            progress.entry_id,
        )
        app = TranscriptApp(view)
        async with app.run_test() as pilot:
            await view.populate_entries((progress, debug, sibling))
            progress_widget = widget_for(view, progress.entry_id)
            sibling_widget = widget_for(view, sibling.entry_id)
            await view.update_entry(completed)
            await pilot.pause()

            completed_card = progress_widget.query_one(Collapsible)
            completed_card.collapsed = False
            completed_card.can_focus = True
            completed_card.focus()
            await view.set_detail_level(EventDetailLevel.DEBUG)
            await view.set_detail_level(EventDetailLevel.NORMAL)
            await pilot.pause()

            assert widget_for(view, progress.entry_id) is progress_widget
            assert widget_for(view, sibling.entry_id) is sibling_widget
            assert progress_widget.query_one(Collapsible) is completed_card
            assert not completed_card.collapsed
            assert app.focused is completed_card
            assert not widget_for(view, debug.entry_id).display

    asyncio.run(scenario())


def test_only_a_real_session_switch_clears_mounted_history() -> None:
    async def scenario() -> None:
        view = CountingTranscript()
        initial = entry("initial")
        app = TranscriptApp(view)
        async with app.run_test() as pilot:
            await view.populate_entries((initial,))
            await view.add_entry(entry("added"))
            await view.update_entry(TranscriptEntry(
                "agent", "initial updated", EventDetailLevel.NORMAL,
                {"type": "message"}, initial.entry_id,
            ))
            await view.set_detail_level(EventDetailLevel.DEBUG)
            await view.delete_entry(initial.entry_id)
            assert view.clear_count == 0

            await view.switch_session("one", (entry("ignored"),))
            assert view.clear_count == 0
            await view.switch_session("two", (entry("new session"),))
            await pilot.pause()
            assert view.clear_count == 1
            assert [row.content for row in view.entries] == ["new session"]

    asyncio.run(scenario())


from pathlib import Path
from pairo.core.agent import AgentConfiguration, SandboxPermission
from pairo.core.clients import FakeAgent
from pairo.ui.sqlite_session_repository import SqliteSessionRepository
from pairo.ui.widgets.agent_chat_pane import AgentChatPane


class DelayedRepository:
    def __init__(self, repository: SqliteSessionRepository, delayed: str) -> None:
        self._repository = repository
        self._delayed = delayed
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def __getattr__(self, name: str):
        return getattr(self._repository, name)

    async def entries_for(self, agent_id: UUID, session_name: str):
        if session_name == self._delayed:
            self.started.set()
            await self.release.wait()
        return await self._repository.entries_for(agent_id, session_name)


class PaneApp(App[None]):
    def __init__(self, pane: AgentChatPane) -> None:
        super().__init__()
        self.pane = pane

    def compose(self) -> ComposeResult:
        yield self.pane


def test_delayed_selected_history_replays_mutations_only_in_selected_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        agent_id = uuid4()
        agent = FakeAgent(AgentConfiguration(id=agent_id, name="race", sandbox=SandboxPermission.READ_ONLY, workspace=tmp_path, model="test"), pipeline, lambda: "ok")
        one = await agent.new_session("one")
        two = await agent.new_session("two")
        repository = SqliteSessionRepository()
        await repository.save_session(agent_id, one.name, one.identifier.model_dump(mode="json"))
        await repository.save_session(agent_id, two.name, two.identifier.model_dump(mode="json"))
        await agent.activate_session(one.name, one.identifier.model_dump(mode="json"))
        old = entry("old")
        base = entry("base")
        added = entry("added")
        updated = TranscriptEntry("agent", "base updated", EventDetailLevel.NORMAL, {"type": "message"}, base.entry_id)
        await repository.add_entry(agent_id, "one", old)
        await repository.add_entry(agent_id, "two", base)
        delayed = DelayedRepository(repository, "")
        pane = AgentChatPane(agent, {agent_id: agent}, delayed, pipeline, default_transcribers(), EventDetailLevel.NORMAL)
        async with PaneApp(pane).run_test() as pilot:
            for _ in range(5):
                await pilot.pause()
            delayed._delayed = "two"
            selecting = asyncio.create_task(pane.select_session("two"))
            await delayed.started.wait()
            adding = asyncio.create_task(pane.add_entry("two", added))
            updating = asyncio.create_task(pane.update_entry("two", updated))
            deleting = asyncio.create_task(pane.delete_entry("two", added.entry_id))
            await pilot.pause()
            assert [row.content for row in pane.transcript.entries] == []
            delayed.release.set()
            await selecting
            await adding
            await updating
            await deleting
            await pilot.pause()
            assert pane.session_name == "two"
            assert [row.content for row in pane.transcript.entries] == ["base updated"]
        await agent.close()
        await repository.close()
    asyncio.run(scenario())
