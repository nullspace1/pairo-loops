"""Spec-manager task lifecycle and temporary-session tests."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from pairo import AgentConfiguration, SandboxPermission
from pairo.core.clients.client import MCP_AGENT_ID_HEADER
from pairo.core.mcp.spec_manager.spec_manager import (
    SpecManager,
    WorkerVerifierPair,
    install_spec_manager,
)


class AgentStub:
    def __init__(self, folder: Path, name: str) -> None:
        self._configuration = AgentConfiguration(
            name=name,
            sandbox=SandboxPermission.READ_ONLY,
            workspace=folder,
            model="test",
        )
        self.created: list[str] = []
        self.messages: list[tuple[str, str]] = []

    def configuration(self) -> AgentConfiguration:
        return self._configuration

    async def new_session(self, name: str):
        self.created.append(name)
        return SimpleNamespace(name=name)

    async def send_message(self, message: str, source: str = "user") -> None:
        self.messages.append((message, source))


def context(agent: AgentStub):
    return SimpleNamespace(
        headers={MCP_AGENT_ID_HEADER: str(agent.configuration().id)}
    )


def tool(manager: SpecManager, name: str):
    return manager._mcp_server._tool_manager.get_tool(name).fn


def setup_manager(tmp_path: Path, timeout: float = 1):
    director = AgentStub(tmp_path, "director")
    worker = AgentStub(tmp_path, "worker")
    verifier = AgentStub(tmp_path, "verifier")
    agents = {
        agent.configuration().id: agent
        for agent in (director, worker, verifier)
    }
    pair = WorkerVerifierPair("pair-1", worker, verifier)
    manager = install_spec_manager(
        tmp_path,
        agents,
        director,
        [pair],
        verification_timeout=timeout,
    )
    return manager, agents, director, worker, verifier


def test_installer_configures_role_specific_skills_and_instructions(tmp_path: Path) -> None:
    manager, _, director, worker, verifier = setup_manager(tmp_path)

    configurations = [agent.configuration() for agent in (director, worker, verifier)]
    assert all(
        configured.mcp_servers == (manager.as_http_mcp_server(),)
        for configured in configurations
    )
    assert director.configuration().skill_folders[0].name == "director"
    assert worker.configuration().skill_folders[0].name == "worker"
    assert verifier.configuration().skill_folders[0].name == "verifier"
    assert "create, modify, delete, and assign" in director.configuration().developer_instructions


def test_assignment_creates_and_reuses_the_verifier_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, verifier = setup_manager(tmp_path)
        assert await tool(manager, "create_task")(
            "Login", "Add login", "A user can sign in", context(director)
        ) == "Created task 'Login'."
        assert (tmp_path / "FEATURES.md").read_text() == (
            "Login: Add login [acceptance: A user can sign in] [status: pending]\n"
        )
        assert "Assigned" in await tool(manager, "assign_task")(
            "pair-1", "Login", context(director)
        )
        worker_session = worker.created[-1]
        verifier_session = verifier.created[-1]
        assert worker_session.startswith("spec-manager-work-pair-1-Login-")
        assert verifier_session.startswith("spec-manager-verify-pair-1-Login-")

        waiting = asyncio.create_task(tool(manager, "verify_task")(context(worker)))
        await asyncio.sleep(0)
        assert "Rejected" in await tool(manager, "reject_task")(
            "Missing failure test", context(verifier)
        )
        assert await waiting == "Missing failure test"
        assert "[status: working]" in (tmp_path / "FEATURES.md").read_text()

        waiting = asyncio.create_task(tool(manager, "verify_task")(context(worker)))
        await asyncio.sleep(0)
        assert verifier.created[-1] == verifier_session
        assert "Accepted" in await tool(manager, "accept_task")(context(verifier))
        assert await waiting == "Task accepted."
        assert "[status: accepted]" in (tmp_path / "FEATURES.md").read_text()
        workers = await tool(manager, "list_workers")(context(director))
        assert workers[0]["status"] == "idle"

    asyncio.run(scenario())


def test_director_can_sleep_until_an_assigned_task_is_accepted(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, verifier = setup_manager(tmp_path)
        await tool(manager, "create_task")("Task", "Do it", "It works", context(director))
        await tool(manager, "assign_task")("pair-1", "Task", context(director))

        sleeping = asyncio.create_task(tool(manager, "sleep_on_task")("Task", context(director)))
        await asyncio.sleep(0)
        assert not sleeping.done()

        verification = asyncio.create_task(tool(manager, "verify_task")(context(worker)))
        await asyncio.sleep(0)
        assert "Accepted" in await tool(manager, "accept_task")(context(verifier))

        assert await verification == "Task accepted."
        assert await sleeping == "Accepted task 'Task'."

    asyncio.run(scenario())


def test_timeout_keeps_verification_active_until_late_acceptance(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, verifier = setup_manager(tmp_path, timeout=0.01)
        await tool(manager, "create_task")("Task", "Do it", "It works", context(director))
        await tool(manager, "assign_task")("pair-1", "Task", context(director))

        result = await tool(manager, "verify_task")(context(worker))
        assert "timed out" in result
        assert "Accepted" in await tool(manager, "accept_task")(context(verifier))

    asyncio.run(scenario())


def test_role_checks_and_unrelated_file_content_are_preserved(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, _ = setup_manager(tmp_path)
        features = tmp_path / "FEATURES.md"
        features.write_text("# Features\n\nNotes stay here.\n", encoding="utf-8")

        denied = await tool(manager, "create_task")("Nope", "No", "No", context(worker))
        assert "Only the registered director" in denied
        await tool(manager, "create_task")("Keep", "Build it", "Tests pass", context(director))
        assert features.read_text(encoding="utf-8").startswith("# Features\n\nNotes stay here.\n")

    asyncio.run(scenario())


def test_director_can_modify_and_delete_pending_tasks(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, _ = setup_manager(tmp_path)
        await tool(manager, "create_task")("Task", "Draft", "Draft works", context(director))

        assert await tool(manager, "modify_task")(
            "Task", "Final description", "Final criteria", context(director)
        ) == "Modified task 'Task'."
        assert (tmp_path / "FEATURES.md").read_text(encoding="utf-8") == (
            "Task: Final description [acceptance: Final criteria] [status: pending]\n"
        )
        assert "Only the registered director" in await tool(manager, "delete_task")(
            "Task", context(worker)
        )
        assert await tool(manager, "delete_task")("Task", context(director)) == "Deleted task 'Task'."
        assert not (tmp_path / "FEATURES.md").read_text(encoding="utf-8")

    asyncio.run(scenario())


def test_director_cannot_modify_or_delete_an_assigned_task(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, _, _ = setup_manager(tmp_path)
        await tool(manager, "create_task")("Task", "Do it", "It works", context(director))
        await tool(manager, "assign_task")("pair-1", "Task", context(director))

        assert "working, not pending" in await tool(manager, "modify_task")(
            "Task", "Changed", "Changed", context(director)
        )
        assert "working, not pending" in await tool(manager, "delete_task")(
            "Task", context(director)
        )

    asyncio.run(scenario())
