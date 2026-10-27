import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Select, Static

from pairo.ui.session_repository import SessionNotFoundError, StatisticValue
from pairo.ui.sqlite_session_repository import SqliteSessionRepository
from pairo.ui.widgets.agent_sidebar import AgentSidebar
from pairo.ui.widgets.sidebar_field import SidebarField
from pairo.ui.widgets.sidebar_statistics import SidebarStatistic, SidebarStatisticCategory


class ControlledRepository(SqliteSessionRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.failure: Exception | None = None

    async def session_statistics_for(self, agent_id: UUID, session_name: str) -> tuple[StatisticValue, ...]:
        if session_name == 'old':
            self.started.set()
            await self.release.wait()
            if self.failure is not None:
                raise self.failure
        return await super().session_statistics_for(agent_id, session_name)


class RepositoryStatistic(SidebarStatistic):
    def __init__(self, repository: ControlledRepository) -> None:
        super().__init__('value', 'Value', SidebarStatisticCategory.SESSION)
        self.repository = repository

    async def value(self, agent_id: UUID, session_name: str | None) -> str | None:
        if session_name is None:
            return None
        values = await self.repository.session_statistics_for(agent_id, session_name)
        return values[0].value if values else None


class SidebarApp(App[None]):
    def __init__(self, sidebar: AgentSidebar) -> None:
        super().__init__()
        self.sidebar = sidebar

    def compose(self) -> ComposeResult:
        yield self.sidebar


def test_deleted_pending_load_preserves_replacement(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = ControlledRepository(tmp_path / 'race.sqlite3')
        agent_id = uuid4()
        for name in ('old', 'replacement'):
            await repo.save_session(agent_id, name, {'name': name})
            await repo.upsert_session_statistic(agent_id, name, StatisticValue('value', name))
        sidebar = AgentSidebar(agent_id, (RepositoryStatistic(repo),))
        app = SidebarApp(sidebar)
        async with app.run_test() as pilot:
            sidebar.query_one(Select).value = 'session'
            pending = asyncio.create_task(sidebar.load('old'))
            await repo.started.wait()
            await repo.delete_session(agent_id, 'old')
            await sidebar.load('replacement')
            repo.release.set()
            await pending
            await pilot.pause()
            fields = list(sidebar.query(SidebarField))
            assert len(fields) == 1
            assert str(fields[0].query_one('.sidebar-field-value', Static).render()) == 'replacement'
            assert all(str(field.query_one('.sidebar-field-value', Static).render()) != 'old' for field in fields)
        await repo.close()
    asyncio.run(scenario())


def test_repository_errors_propagate_even_for_stale_load(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = ControlledRepository(tmp_path / 'errors.sqlite3')
        agent_id = uuid4()
        await repo.save_session(agent_id, 'replacement', {'name': 'replacement'})
        await repo.upsert_session_statistic(agent_id, 'replacement', StatisticValue('value', 'working'))
        sidebar = AgentSidebar(agent_id, (RepositoryStatistic(repo),))
        async with SidebarApp(sidebar).run_test() as pilot:
            sidebar.query_one(Select).value = 'session'
            pending = asyncio.create_task(sidebar.load('old'))
            await repo.started.wait()
            await sidebar.load('replacement')
            repo.failure = RuntimeError('repository failure')
            repo.release.set()
            with pytest.raises(RuntimeError, match='repository failure'):
                await pending
            await pilot.pause()
            assert str(sidebar.query_one('.sidebar-field-value', Static).render()) == 'working'
            with pytest.raises(SessionNotFoundError):
                await sidebar.load('missing')
        await repo.close()
    asyncio.run(scenario())
