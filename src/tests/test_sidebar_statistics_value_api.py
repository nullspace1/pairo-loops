"""Focused scoped sidebar-statistic API tests."""

import asyncio
from pathlib import Path
from uuid import uuid4

from textual.app import App
from textual.message import Message

from pairo.core.agent import AgentConfiguration, SandboxPermission
from pairo.core.clients import FakeAgent
from pairo.core.events import AgentTokenUsageUpdated, EventPipeline, TokenUsage
from pairo.ui.configuration import UserInterfaceConfiguration
from pairo.ui.pairo_event_piper import PairoEvent
from pairo.ui.sqlite_session_repository import SqliteSessionRepository
from pairo.ui.widgets.sidebar_statistics import (
    FixedStatistic,
    SidebarStatistic,
    SidebarStatisticCategory,
    SidebarStatisticFields,
    TokenUsageStatistic,
    UpdateSessionStatistic,
    create_default_sidebar_statistics,
    default_sidebar_statistics,
)


class RecordingApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[object] = []

    def post_message(self, message: Message) -> bool:
        self.messages.append(message)
        return True


def _agent(tmp_path: Path, name: str) -> FakeAgent:
    return FakeAgent(
        AgentConfiguration(
            id=uuid4(), name=name, model="test", workspace=tmp_path,
            sandbox=SandboxPermission.READ_ONLY,
        ),
        EventPipeline(),
        lambda: "done",
    )


def _usage(total: int) -> TokenUsage:
    return TokenUsage(
        input_tokens=1,
        cached_input_tokens=0,
        output_tokens=1,
        reasoning_output_tokens=0,
        total_tokens=total,
    )


def test_shared_statistic_sequence_derives_ordered_fields_and_is_snapshotted(
    tmp_path: Path,
) -> None:
    first = FixedStatistic("first", "First", _constant)
    second = FixedStatistic(
        "second", "Second", _constant, SidebarStatisticCategory.SESSION
    )
    configured = UserInterfaceConfiguration(sidebar_statistics=[first, second])

    assert configured.sidebar_statistics == (first, second)
    assert SidebarStatisticFields(configured.sidebar_statistics).agent_fields == (
        first.field,
    )
    assert SidebarStatisticFields(configured.sidebar_statistics).session_fields == (
        second.field,
    )
    assert all(isinstance(statistic, SidebarStatistic) for statistic in default_sidebar_statistics)
    assert not hasattr(SidebarStatistic, "build")
    assert not hasattr(SidebarStatistic, "compute")
    assert not hasattr(SidebarStatistic, "has_initial_value")


async def _constant(_agent: FakeAgent, _session: object) -> object:
    return "constant"


def test_fixed_values_and_token_updates_are_scoped_and_durable(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SqliteSessionRepository(tmp_path / "statistics.sqlite3")
        app = RecordingApp()
        first, second = _agent(tmp_path, "first"), _agent(tmp_path, "second")
        first_session = await first.new_session("one")
        second_session = await second.new_session("two")
        first_id, second_id = first.configuration().id, second.configuration().id
        await repository.save_session(
            first_id, first_session.name, first_session.identifier.model_dump(mode="json")
        )
        await repository.save_session(
            second_id, second_session.name, second_session.identifier.model_dump(mode="json")
        )
        name = next(statistic for statistic in create_default_sidebar_statistics() if statistic.key == "name")
        token = TokenUsageStatistic()
        await name.configure(repository, app, (first, second))
        await token.configure(repository, app, (first, second))

        assert await name.value(first_id, first_session.name) == "first"
        assert await name.value(second_id, second_session.name) == "second"
        assert await token.value(first_id, first_session.name) == "Unavailable"

        await token.receive_usage(
            PairoEvent(AgentTokenUsageUpdated(
                agent_id=first_id,
                session_name=first_session.name,
                last=_usage(4),
                total=_usage(41),
            ))
        )

        assert await token.value(first_id, first_session.name) == "41 tokens"
        assert await token.value(second_id, second_session.name) == "Unavailable"
        notification = app.messages[-1]
        assert isinstance(notification, UpdateSessionStatistic)
        assert (notification.agent_id, notification.session_name, notification.value) == (
            first_id,
            first_session.name,
            "41 tokens",
        )
        await token.teardown()
        await name.teardown()
        await repository.close()

    asyncio.run(scenario())
