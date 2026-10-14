"""Spec-manager task lifecycle and temporary-session tests."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from pairo import Agent, SandboxMode
from pairo.core.clients.client import MCP_AGENT_ID_HEADER
from pairo.core.mcp.spec_manager import (
    SpecManager,
    WorkerVerifierPair,
    install_spec_manager,
)


def agent(folder: Path, name: str) -> Agent:
    return Agent(name=name, sandbox=SandboxMode.READ_ONLY, workspace=folder, model="test")


class RunnerStub:
    def __init__(self, agents):
        self.agents = {configured.id: configured for configured in agents}
        self.sessions = {configured.id: ["Conversation 1"] for configured in agents}
        self.created = []
        self.deleted = []
        self.messages = []

    async def create_session(self, agent_id, name):
        self.created.append((agent_id, name))
        self.sessions[agent_id].append(name)
        return name

    async def send_message(self, agent_id, message, source="user"):
        self.messages.append((agent_id, message, source))

    async def delete_session(self, agent_id, name):
        self.deleted.append((agent_id, name))
        self.sessions[agent_id].remove(name)
        return self.sessions[agent_id][-1] if self.sessions[agent_id] else None

    def session_names(self, agent_id):
        return tuple(self.sessions[agent_id])

    def is_generating(self, agent_id):
        return False


def context(configured: Agent):
    return SimpleNamespace(headers={MCP_AGENT_ID_HEADER: str(configured.id)})


def tool(manager: SpecManager, name: str):
    return manager._mcp_server._tool_manager.get_tool(name).fn


def setup_manager(tmp_path: Path, timeout: float = 1):
    director = agent(tmp_path, "director")
    worker = agent(tmp_path, "worker")
    verifier = agent(tmp_path, "verifier")
    runner = RunnerStub([director, worker, verifier])
    pair = WorkerVerifierPair("pair-1", worker, verifier)
    manager = install_spec_manager(tmp_path, runner, director, [pair], verification_timeout=timeout)
    return manager, runner, director, worker, verifier


def test_installer_configures_role_specific_skills_and_instructions(tmp_path: Path) -> None:
    manager, _, director, worker, verifier = setup_manager(tmp_path)

    assert all(configured.mcp_servers == (manager.as_http_mcp_server(),) for configured in (director, worker, verifier))
    assert director.skill_folders[0].name == "director"
    assert worker.skill_folders[0].name == "worker"
    assert verifier.skill_folders[0].name == "verifier"
    assert "create, list, and assign" in director.developer_instructions


def test_create_assign_reject_and_accept_clears_verifier_sessions(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, runner, director, worker, verifier = setup_manager(tmp_path)
        assert await tool(manager, "create_task")(
            "Login", "Add login", "A user can sign in", context(director)
        ) == "Created task 'Login'."
        assert (tmp_path / "FEATURES.md").read_text() == (
            "Login: Add login [acceptance: A user can sign in] [status: pending]\n"
        )
        assert "Assigned" in await tool(manager, "assign_task")(
            "pair-1", "Login", context(director)
        )

        waiting = asyncio.create_task(tool(manager, "verify_task")(context(worker)))
        await asyncio.sleep(0)
        verifier_session = runner.created[-1][1]
        assert "Rejected" in await tool(manager, "reject_task")(
            "Missing failure test", context(verifier)
        )
        assert await waiting == "Missing failure test"
        assert (verifier.id, verifier_session) in runner.deleted
        assert "[status: working]" in (tmp_path / "FEATURES.md").read_text()

        waiting = asyncio.create_task(tool(manager, "verify_task")(context(worker)))
        await asyncio.sleep(0)
        verifier_session = runner.created[-1][1]
        assert "Accepted" in await tool(manager, "accept_task")(context(verifier))
        assert await waiting == "Task accepted."
        assert (verifier.id, verifier_session) in runner.deleted
        assert "[status: accepted]" in (tmp_path / "FEATURES.md").read_text()
        workers = await tool(manager, "list_workers")(context(director))
        assert workers[0]["status"] == "idle"

    asyncio.run(scenario())


def test_timeout_keeps_verification_active_until_late_acceptance(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, runner, director, worker, verifier = setup_manager(tmp_path, timeout=0.01)
        await tool(manager, "create_task")("Task", "Do it", "It works", context(director))
        await tool(manager, "assign_task")("pair-1", "Task", context(director))

        result = await tool(manager, "verify_task")(context(worker))
        verifier_session = runner.created[-1][1]
        assert "timed out" in result
        assert (verifier.id, verifier_session) not in runner.deleted
        assert "Accepted" in await tool(manager, "accept_task")(context(verifier))
        assert (verifier.id, verifier_session) in runner.deleted

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
