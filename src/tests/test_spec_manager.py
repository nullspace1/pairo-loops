"""Spec-manager task lifecycle and temporary-session tests."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pairo import AgentConfiguration, SandboxPermission
from pairo.core.clients.client import MCP_AGENT_ID_HEADER
from pairo.core.mcp.mcp_http_wrapper import MCPHttpWrapper
from pairo.mcp.spec_manager import (
    AcceptanceEvidence,
    SpecManager,
    TestRunSummary as RunSummary,
    VerificationEvidence,
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


def verification_evidence() -> VerificationEvidence:
    return VerificationEvidence(
        implementation_summary=(
            "Moved assignment waiter cancellation before graceful transport shutdown."
        ),
        changed_files=[
            "pairo/mcp/spec_manager/spec_manager.py",
            "tests/test_spec_manager.py",
        ],
        acceptance_evidence=[
            AcceptanceEvidence(
                criterion="Shutdown completes with an active assignment waiter.",
                evidence="Covered by test_close_cancels_sleep_waiter_before_stopping_http_transport.",
            )
        ],
        test_runs=[
            RunSummary(
                command=(
                    ".venv\\Scripts\\python.exe -m pytest "
                    "tests/test_spec_manager.py -q -p no:cacheprovider"
                ),
                outcome="passed",
                passed=10,
                failed=0,
                skipped=0,
                duration_seconds=1.2,
                summary="Covered assignment, verification, and shutdown lifecycles.",
            )
        ],
        remaining_risks=["No live network transport was used."],
    )


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

        waiting = asyncio.create_task(
            tool(manager, "verify_task")(verification_evidence(), context(worker))
        )
        await asyncio.sleep(0)
        verifier_message = verifier.messages[-1][0]
        assert "Worker evidence:" in verifier_message
        assert "test_close_cancels_sleep_waiter" in verifier_message
        assert "No live network transport was used." in verifier_message
        assert "Rejected" in await tool(manager, "reject_task")(
            "Missing failure test", context(verifier)
        )
        assert await waiting == "Missing failure test"
        assert "[status: working]" in (tmp_path / "FEATURES.md").read_text()

        waiting = asyncio.create_task(
            tool(manager, "verify_task")(verification_evidence(), context(worker))
        )
        await asyncio.sleep(0)
        assert verifier.created[-1] == verifier_session
        assert "Accepted" in await tool(manager, "accept_task")(context(verifier))
        assert await waiting == "Task accepted."
        assert "[status: accepted]" in (tmp_path / "FEATURES.md").read_text()
        workers = await tool(manager, "list_workers")(context(director))
        assert workers[0]["status"] == "idle"

    asyncio.run(scenario())


def test_accepted_feature_history_is_referenced_only_to_documenter(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        director = AgentStub(tmp_path, "director")
        worker = AgentStub(tmp_path, "worker")
        verifier = AgentStub(tmp_path, "verifier")
        documenter = AgentStub(tmp_path, "documenter")
        agents = {
            agent.configuration().id: agent
            for agent in (director, worker, verifier, documenter)
        }
        manager = install_spec_manager(
            tmp_path,
            agents,
            director,
            [WorkerVerifierPair("pair-1", worker, verifier)],
            documenter=documenter,
        )

        history = tmp_path / "output" / "FEATURE_HISTORY.md"
        assert history.read_text(encoding="utf-8") == "# Feature history\n\n"
        assert documenter.configuration().mcp_servers == ()
        assert documenter.configuration().skill_folders == ()

        await tool(manager, "create_task")(
            "History", "Record accepted work", "Evidence is retained", context(director)
        )
        await tool(manager, "assign_task")(
            "pair-1", "History", context(director)
        )
        verification = asyncio.create_task(
            tool(manager, "verify_task")(verification_evidence(), context(worker))
        )
        await asyncio.sleep(0)
        assert "Accepted" in await tool(manager, "accept_task")(context(verifier))
        assert await verification == "Task accepted."

        content = history.read_text(encoding="utf-8")
        assert "## History" in content
        assert "Record accepted work" in content
        assert "### Implementation" in content
        assert "### Acceptance evidence" in content
        assert "### Verification" in content
        assert "No live network transport was used." in content

        assert len(documenter.messages) == 1
        reference, source = documenter.messages[0]
        assert source == "spec_manager"
        assert reference == f"Feature history updated: {history.resolve()}"
        assert "Moved assignment waiter cancellation" not in reference
        assert director.messages == []

    asyncio.run(scenario())


def test_director_can_sleep_until_an_assigned_task_is_accepted(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, verifier = setup_manager(tmp_path)
        await tool(manager, "create_task")("Task", "Do it", "It works", context(director))
        await tool(manager, "assign_task")("pair-1", "Task", context(director))

        sleeping = asyncio.create_task(tool(manager, "sleep_on_task")("Task", context(director)))
        await asyncio.sleep(0)
        assert not sleeping.done()

        verification = asyncio.create_task(
            tool(manager, "verify_task")(verification_evidence(), context(worker))
        )
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

        result = await tool(manager, "verify_task")(
            verification_evidence(), context(worker)
        )
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


def test_close_cancels_sleep_waiter_before_stopping_http_transport(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, _, _ = setup_manager(tmp_path)
        await tool(manager, "create_task")("Task", "Do it", "It works", context(director))
        await tool(manager, "assign_task")("pair-1", "Task", context(director))
        sleeping = asyncio.create_task(tool(manager, "sleep_on_task")("Task", context(director)))
        await asyncio.sleep(0)

        transport_observations: list[tuple[bool, bool]] = []

        async def close_transport(_wrapper: MCPHttpWrapper) -> None:
            assignment = manager._assignments["pair-1"]
            transport_observations.append(
                (assignment.completion.cancelled(), assignment.review_result is None)
            )
            # A graceful transport waits for active tool handlers.  The
            # cancelled waiter must therefore have released this handler.
            try:
                await sleeping
            except asyncio.CancelledError:
                pass

        with patch.object(MCPHttpWrapper, "close", close_transport):
            await asyncio.wait_for(manager.close(), timeout=1)
            await asyncio.wait_for(manager.close(), timeout=1)

        assert transport_observations == [(True, True), (True, True)]
        assert sleeping.cancelled()
        assert (tmp_path / "FEATURES.md").read_text(encoding="utf-8") == (
            "Task: Do it [acceptance: It works] [status: working]\n"
        )

    asyncio.run(scenario())


def test_close_cancels_verification_waiter_before_stopping_http_transport(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, _ = setup_manager(tmp_path)
        await tool(manager, "create_task")("Task", "Do it", "It works", context(director))
        await tool(manager, "assign_task")("pair-1", "Task", context(director))
        verifying = asyncio.create_task(
            tool(manager, "verify_task")(verification_evidence(), context(worker))
        )
        await asyncio.sleep(0)

        transport_observations: list[tuple[bool, bool]] = []

        async def close_transport(_wrapper: MCPHttpWrapper) -> None:
            assignment = manager._assignments["pair-1"]
            transport_observations.append(
                (
                    assignment.completion.cancelled(),
                    assignment.review_result is not None and assignment.review_result.cancelled(),
                )
            )
            # Model Uvicorn's graceful wait for the in-flight tool request.
            try:
                await verifying
            except asyncio.CancelledError:
                pass

        with patch.object(MCPHttpWrapper, "close", close_transport):
            await asyncio.wait_for(manager.close(), timeout=1)
            await asyncio.wait_for(manager.close(), timeout=1)

        assert transport_observations == [(True, True), (True, True)]
        assert verifying.cancelled()
        assert (tmp_path / "FEATURES.md").read_text(encoding="utf-8") == (
            "Task: Do it [acceptance: It works] [status: verifying]\n"
        )

    asyncio.run(scenario())
