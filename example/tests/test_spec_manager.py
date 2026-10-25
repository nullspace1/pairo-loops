"""Spec-manager task lifecycle and temporary-session tests."""

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from pairo import AgentConfiguration, AgentStatus, SandboxPermission
from pairo.core.clients.client import MCP_AGENT_ID_HEADER
from pairo.core.mcp.mcp_http_wrapper import MCPHttpWrapper
from spec_manager import (
    AcceptanceCriterion,
    AcceptanceEvidence,
    CorrectionResolution,
    SpecManager,
    TestRunSummary as RunSummary,
    UnsuccessfulWorkReport,
    VerificationEvidence,
    VerificationCorrection,
    WorkerVerifierPair,
    install_spec_manager,
)


CRITERIA = [AcceptanceCriterion(id="AC1", description="It works")]


def test_spec_manager_is_example_local_and_not_exported_by_pairo() -> None:
    package = importlib.import_module("spec_manager")
    main = importlib.import_module("main")
    pairo = importlib.import_module("pairo")
    assert package.SpecManager.__module__ == "spec_manager.spec_manager"
    assert main.install_spec_manager is package.install_spec_manager
    assert not hasattr(pairo, "SpecManager")
    assert not hasattr(pairo, "install_spec_manager")


def test_unsuccessful_work_report_requires_a_nonblank_bounded_reason() -> None:
    with pytest.raises(ValidationError, match="cannot be blank"):
        UnsuccessfulWorkReport(reason="   ")
    with pytest.raises(ValidationError):
        UnsuccessfulWorkReport(reason="Blocked.", partial_work="x" * 4_001)


class AgentStub:
    def __init__(self, folder: Path, name: str) -> None:
        self._configuration = AgentConfiguration(
            id=uuid4(),
            name=name,
            sandbox=SandboxPermission.READ_ONLY,
            workspace=folder,
            model="test",
        )
        self.created: list[str] = []
        self.messages: list[tuple[str, str]] = []
        self.state = AgentStatus.IDLE

    def configuration(self) -> AgentConfiguration:
        return self._configuration

    def status(self) -> AgentStatus:
        return self.state

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


def verification_evidence(
    resolutions: list[CorrectionResolution] | None = None,
) -> VerificationEvidence:
    return VerificationEvidence(
        implementation_summary=(
            "Moved assignment waiter cancellation before graceful transport shutdown."
        ),
        changed_files=[
            "example/spec_manager/spec_manager.py",
            "tests/test_spec_manager.py",
        ],
        acceptance_evidence=[
            AcceptanceEvidence(
                criterion_id="AC1",
                evidence="Covered by test_close_cancels_sleep_waiter_before_stopping_http_transport.",
                test_run_ids=["TR1"],
            )
        ],
        test_runs=[
            RunSummary(
                id="TR1",
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
        correction_resolutions=resolutions or [],
        remaining_risks=["No live network transport was used."],
    )


def setup_manager(
    tmp_path: Path, timeout: float = 1, handoff_timeout: float = 1
):
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
        handoff_timeout=handoff_timeout,
        handoff_poll_interval=0.001,
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
    assert "do not generate a plan" in worker.configuration().developer_instructions
    assert "report_unsuccessful_work" in worker.configuration().developer_instructions
    assert "stop safely" in worker.configuration().developer_instructions
    assert "criterion ID" in worker.configuration().developer_instructions
    assert "structured corrections" in verifier.configuration().developer_instructions


def test_assignment_creates_and_reuses_the_verifier_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, verifier = setup_manager(tmp_path)
        assert await tool(manager, "create_task")(
            "Login", "Add login", CRITERIA, context(director)
        ) == "Created task 'Login'."
        content = (tmp_path / "FEATURES.md").read_text()
        assert "```pairo-task" in content
        assert '"id": "AC1"' in content
        assert '"status": "pending"' in content
        assert "Assigned" in await tool(manager, "assign_task")(
            "pair-1", "Login", context(director)
        )
        worker_session = worker.created[-1]
        verifier_session = verifier.created[-1]
        assert worker_session.startswith("spec-manager-work-pair-1-Login-")
        assert verifier_session.startswith("spec-manager-verify-pair-1-Login-")
        worker_message = worker.messages[-1][0]
        assert "do not create a second implementation plan" in worker_message
        assert "focused verification" in worker_message

        waiting = asyncio.create_task(
            tool(manager, "verify_task")(verification_evidence(), context(worker))
        )
        await asyncio.sleep(0)
        verifier_message = verifier.messages[-1][0]
        assert "Worker evidence:" in verifier_message
        assert "test_close_cancels_sleep_waiter" in verifier_message
        assert "No live network transport was used." in verifier_message
        assert "Rejected" in await tool(manager, "reject_task")(
            [VerificationCorrection(
                id="C1", criterion_ids=["AC1"], message="Missing failure test"
            )],
            context(verifier),
        )
        assert await waiting == "1 correction(s) required."
        assert '"status": "working"' in (tmp_path / "FEATURES.md").read_text()

        waiting = asyncio.create_task(
            tool(manager, "verify_task")(
                verification_evidence([
                    CorrectionResolution(
                        correction_id="C1", resolution="Added the missing failure test."
                    )
                ]),
                context(worker),
            )
        )
        await asyncio.sleep(0)
        assert verifier.created[-1] == verifier_session
        assert "Accepted" in await tool(manager, "accept_task")(context(verifier))
        assert await waiting == "Task accepted."
        assert '"status": "accepted"' in (tmp_path / "FEATURES.md").read_text()
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
            "History", "Record accepted work", CRITERIA, context(director)
        )
        await tool(manager, "assign_task")(
            "pair-1", "History", context(director)
        )
        verification = asyncio.create_task(
            tool(manager, "verify_task")(verification_evidence(), context(worker))
        )
        await asyncio.sleep(0)
        documenter.state = AgentStatus.GENERATING
        decision = asyncio.create_task(tool(manager, "accept_task")(context(verifier)))
        await asyncio.sleep(0.01)
        assert documenter.messages == []
        documenter.state = AgentStatus.IDLE
        assert "Accepted" in await decision
        assert await verification == "Task accepted."

        content = history.read_text(encoding="utf-8")
        assert "## History" in content
        assert "Record accepted work" in content
        assert "### Implementation" in content
        assert "### Acceptance evidence" in content
        assert "### Verification corrections" in content
        assert "### Correction resolutions" in content
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
        await tool(manager, "create_task")("Task", "Do it", CRITERIA, context(director))
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


def test_director_can_discover_tasks_and_use_a_bounded_wait(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, _, _ = setup_manager(tmp_path)
        await tool(manager, "create_task")(
            "Task", "Do it", CRITERIA, context(director)
        )

        tasks = await tool(manager, "list_tasks")(context(director), "pending")
        assert isinstance(tasks, list)
        assert tasks == [{
            "name": "Task",
            "description": "Do it",
            "status": "pending",
            "acceptance_criteria": [{"id": "AC1", "description": "It works"}],
            "pair_id": None,
            "outstanding_corrections": [],
            "unsuccessful_work": None,
        }]
        assert await tool(manager, "get_task")(
            "Task", context(director)
        ) == tasks[0]

        await tool(manager, "assign_task")(
            "pair-1", "Task", context(director)
        )
        result = await tool(manager, "wait_for_task")(
            "Task", context(director), 0.001
        )
        assert result == {
            "task_name": "Task",
            "status": "still_running",
            "task_status": "working",
        }
        assert not manager._assignments["pair-1"].completion.cancelled()

    asyncio.run(scenario())


def test_timeout_keeps_verification_active_until_late_acceptance(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, verifier = setup_manager(tmp_path, timeout=0.01)
        await tool(manager, "create_task")("Task", "Do it", CRITERIA, context(director))
        await tool(manager, "assign_task")("pair-1", "Task", context(director))

        result = await tool(manager, "verify_task")(
            verification_evidence(), context(worker)
        )
        assert "timed out" in result
        assert "Accepted" in await tool(manager, "accept_task")(context(verifier))

    asyncio.run(scenario())


def test_verification_requires_exact_criterion_and_test_run_mapping(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, _ = setup_manager(tmp_path)
        criteria = [
            AcceptanceCriterion(id="AC1", description="First outcome"),
            AcceptanceCriterion(id="AC2", description="Second outcome"),
        ]
        await tool(manager, "create_task")(
            "Task", "Do it", criteria, context(director)
        )
        await tool(manager, "assign_task")("pair-1", "Task", context(director))

        result = await tool(manager, "verify_task")(
            verification_evidence(), context(worker)
        )

        assert "map exactly once to every task criterion" in result
        assert manager._assignments["pair-1"].status == "working"

    asyncio.run(scenario())


def test_resubmission_requires_every_outstanding_correction(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, verifier = setup_manager(tmp_path)
        await tool(manager, "create_task")(
            "Task", "Do it", CRITERIA, context(director)
        )
        await tool(manager, "assign_task")("pair-1", "Task", context(director))
        verifying = asyncio.create_task(
            tool(manager, "verify_task")(verification_evidence(), context(worker))
        )
        await asyncio.sleep(0)
        await tool(manager, "reject_task")(
            [
                VerificationCorrection(
                    id="C1", criterion_ids=["AC1"], message="Add direct coverage."
                ),
                VerificationCorrection(
                    id="C2", criterion_ids=["AC1"], message="Document the risk."
                ),
            ],
            context(verifier),
        )
        await verifying

        result = await tool(manager, "verify_task")(
            verification_evidence([
                CorrectionResolution(
                    correction_id="C1", resolution="Added direct coverage."
                )
            ]),
            context(worker),
        )

        assert "map exactly once to every open correction" in result
        assert len(verifier.messages) == 1

    asyncio.run(scenario())


def test_legacy_feature_content_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, _ = setup_manager(tmp_path)
        features = tmp_path / "FEATURES.md"
        features.write_text("# Features\n\nNotes stay here.\n", encoding="utf-8")

        denied = await tool(manager, "create_task")("Nope", "No", CRITERIA, context(worker))
        assert "Only the registered director" in denied
        result = await tool(manager, "create_task")(
            "Keep", "Build it", CRITERIA, context(director)
        )
        assert "unsupported format" in result
        assert features.read_text(encoding="utf-8") == "# Features\n\nNotes stay here.\n"

    asyncio.run(scenario())


def test_director_can_modify_and_delete_pending_tasks(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, _ = setup_manager(tmp_path)
        await tool(manager, "create_task")("Task", "Draft", CRITERIA, context(director))

        assert await tool(manager, "modify_task")(
            "Task",
            "Final description",
            [AcceptanceCriterion(id="AC1", description="Final criteria")],
            context(director),
        ) == "Modified task 'Task'."
        content = (tmp_path / "FEATURES.md").read_text(encoding="utf-8")
        assert "Final description" in content
        assert "Final criteria" in content
        assert "Only the registered director" in await tool(manager, "delete_task")(
            "Task", context(worker)
        )
        assert await tool(manager, "delete_task")("Task", context(director)) == "Deleted task 'Task'."
        assert (tmp_path / "FEATURES.md").read_text(encoding="utf-8") == "# Features\n\n"

    asyncio.run(scenario())


def test_director_cannot_modify_or_delete_an_assigned_task(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, _ = setup_manager(tmp_path)
        await tool(manager, "create_task")("Task", "Do it", CRITERIA, context(director))
        await tool(manager, "assign_task")("pair-1", "Task", context(director))

        assert "working, not pending" in await tool(manager, "modify_task")(
            "Task", "Changed", CRITERIA, context(director)
        )
        assert "working, not pending" in await tool(manager, "delete_task")(
            "Task", context(director)
        )

    asyncio.run(scenario())


def test_close_cancels_sleep_waiter_before_stopping_http_transport(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, _ = setup_manager(tmp_path)
        await tool(manager, "create_task")("Task", "Do it", CRITERIA, context(director))
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
        assert '"status": "working"' in (tmp_path / "FEATURES.md").read_text(encoding="utf-8")

    asyncio.run(scenario())


def test_close_cancels_verification_waiter_before_stopping_http_transport(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, _ = setup_manager(tmp_path)
        await tool(manager, "create_task")("Task", "Do it", CRITERIA, context(director))
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
        assert '"status": "verifying"' in (tmp_path / "FEATURES.md").read_text(encoding="utf-8")

    asyncio.run(scenario())


def test_unsuccessful_work_is_authorized_persisted_and_releases_pair(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager, agents, director, worker, verifier = setup_manager(tmp_path)
        await tool(manager, "create_task")("Task", "Do it", CRITERIA, context(director))
        report = UnsuccessfulWorkReport(
            reason="A required service is unavailable.",
            partial_work="Added the isolated parser test.",
        )
        old_tool = "request" + "_specification" + "_change"
        assert manager._mcp_server._tool_manager.get_tool(old_tool) is None
        assert "Only a registered worker" in await tool(manager, "report_unsuccessful_work")(
            report, context(director)
        )
        assert '"status": "pending"' in (tmp_path / "FEATURES.md").read_text()
        await tool(manager, "assign_task")("pair-1", "Task", context(director))
        assert "Only a registered worker" in await tool(manager, "report_unsuccessful_work")(
            report, context(verifier)
        )

        director.state = AgentStatus.GENERATING
        reported = asyncio.create_task(
            tool(manager, "report_unsuccessful_work")(report, context(worker))
        )
        await asyncio.sleep(0.01)
        assert director.messages == []
        assert "pair-1" not in manager._assignments
        assert "Created" in await tool(manager, "create_task")(
            "Planner can proceed", "Record the revised plan", CRITERIA, context(director)
        )
        persisted = (tmp_path / "FEATURES.md").read_text(encoding="utf-8")
        assert '"status": "unsuccessful"' in persisted
        assert "A required service is unavailable." in persisted
        assert "Added the isolated parser test." in persisted

        director.state = AgentStatus.IDLE
        assert "Ended task 'Task' unsuccessfully" in await reported
        assert len(director.messages) == 1
        assert "Component: Task" in director.messages[0][0]
        assert "Worker: worker" in director.messages[0][0]
        assert "Partial work: Added the isolated parser test." in director.messages[0][0]

        reloaded = SpecManager(
            tmp_path, agents, director, [WorkerVerifierPair("pair-1", worker, verifier)]
        )
        task = reloaded._store.tasks()["Task"]
        assert task.status == "unsuccessful"
        assert task.unsuccessful_work == report
        assert "not pending" in await tool(manager, "modify_task")(
            "Task", "Changed", CRITERIA, context(director)
        )
        assert "not pending" in await tool(manager, "delete_task")("Task", context(director))

        await tool(manager, "create_task")("Replacement", "Try another route", CRITERIA, context(director))
        assert "Assigned" in await tool(manager, "assign_task")(
            "pair-1", "Replacement", context(director)
        )

    asyncio.run(scenario())


def test_unsuccessful_work_resolves_waiters_and_rejects_verifying_work(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, verifier = setup_manager(tmp_path)
        await tool(manager, "create_task")("Task", "Do it", CRITERIA, context(director))
        await tool(manager, "assign_task")("pair-1", "Task", context(director))

        sleeping = asyncio.create_task(tool(manager, "sleep_on_task")("Task", context(director)))
        waiting = asyncio.create_task(
            tool(manager, "wait_for_task")("Task", context(director), 1)
        )
        report = UnsuccessfulWorkReport(reason="The dependency cannot be installed.")
        assert "Ended task" in await tool(manager, "report_unsuccessful_work")(
            report, context(worker)
        )
        assert await sleeping == "Task 'Task' ended unsuccessfully: The dependency cannot be installed."
        assert await waiting == {
            "task_name": "Task",
            "status": "unsuccessful",
            "reason": "The dependency cannot be installed.",
            "partial_work": None,
        }
        assert await tool(manager, "wait_for_task")("Task", context(director), 1) == await waiting

        await tool(manager, "create_task")("Review", "Do it", CRITERIA, context(director))
        await tool(manager, "assign_task")("pair-1", "Review", context(director))
        verifier.state = AgentStatus.GENERATING
        verifying = asyncio.create_task(
            tool(manager, "verify_task")(verification_evidence(), context(worker))
        )
        await asyncio.sleep(0)
        assert "no working task" in await tool(manager, "report_unsuccessful_work")(
            report, context(worker)
        )
        assert '"status": "verifying"' in (tmp_path / "FEATURES.md").read_text()
        verifier.state = AgentStatus.IDLE
        await asyncio.sleep(0)
        assert "Rejected" in await tool(manager, "reject_task")(
            [VerificationCorrection(id="C1", criterion_ids=["AC1"], message="Fix it")],
            context(verifier),
        )
        assert "correction(s) required" in await verifying

    asyncio.run(scenario())


def test_unsuccessful_work_notification_timeout_and_shutdown_keep_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, _ = setup_manager(tmp_path, handoff_timeout=0.01)
        await tool(manager, "create_task")("Timeout", "Do it", CRITERIA, context(director))
        await tool(manager, "assign_task")("pair-1", "Timeout", context(director))
        director.state = AgentStatus.GENERATING
        timed_out = await tool(manager, "report_unsuccessful_work")(
            UnsuccessfulWorkReport(reason="Planner is busy."), context(worker)
        )
        assert "Planner notification failed" in timed_out
        assert '"status": "unsuccessful"' in (tmp_path / "FEATURES.md").read_text()
        assert director.messages == []

        shutdown_folder = tmp_path / "shutdown"
        shutdown_folder.mkdir()
        shutdown, _, shutdown_director, shutdown_worker, _ = setup_manager(shutdown_folder)
        await tool(shutdown, "create_task")("Shutdown", "Do it", CRITERIA, context(shutdown_director))
        await tool(shutdown, "assign_task")("pair-1", "Shutdown", context(shutdown_director))
        shutdown_director.state = AgentStatus.GENERATING
        reporting = asyncio.create_task(
            tool(shutdown, "report_unsuccessful_work")(
                UnsuccessfulWorkReport(reason="Shutdown race."), context(shutdown_worker)
            )
        )
        await asyncio.sleep(0)
        assert "pair-1" not in shutdown._assignments
        await shutdown.close()
        assert "Planner notification failed" in await reporting
        assert '"status": "unsuccessful"' in (
            shutdown_folder / "FEATURES.md"
        ).read_text()

    asyncio.run(scenario())


def test_idle_safe_delivery_delays_every_handoff_and_times_out(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _, director, worker, verifier = setup_manager(tmp_path)
        worker.state = AgentStatus.GENERATING
        await tool(manager, "create_task")("Task", "Do it", CRITERIA, context(director))
        assignment = asyncio.create_task(
            tool(manager, "assign_task")("pair-1", "Task", context(director))
        )
        await asyncio.sleep(0.01)
        assert worker.messages == []
        workers = await tool(manager, "list_workers")(context(director))
        assert workers[0]["status"] == "working"
        worker.state = AgentStatus.IDLE
        assert "Assigned" in await assignment

        verifier.state = AgentStatus.GENERATING
        verification = asyncio.create_task(
            tool(manager, "verify_task")(verification_evidence(), context(worker))
        )
        await asyncio.sleep(0.01)
        assert verifier.messages == []
        verifier.state = AgentStatus.IDLE
        await asyncio.sleep(0.01)
        worker.state = AgentStatus.GENERATING
        rejection = asyncio.create_task(tool(manager, "reject_task")(
            [VerificationCorrection(id="C1", criterion_ids=["AC1"], message="Fix it")],
            context(verifier),
        ))
        await asyncio.sleep(0.01)
        assert len(worker.messages) == 1
        worker.state = AgentStatus.IDLE
        assert "Rejected" in await rejection
        assert "correction(s) required" in await verification

        timeout_folder = tmp_path / "timeout"
        timeout_folder.mkdir()
        timeout_manager, _, timeout_director, timeout_worker, _ = setup_manager(
            timeout_folder, handoff_timeout=0.01
        )
        timeout_worker.state = AgentStatus.GENERATING
        await tool(timeout_manager, "create_task")(
            "Timeout", "Do it", CRITERIA, context(timeout_director)
        )
        result = await tool(timeout_manager, "assign_task")(
            "pair-1", "Timeout", context(timeout_director)
        )
        assert "handoff timeout" in result
        assert '"status": "pending"' in (
            tmp_path / "timeout" / "FEATURES.md"
        ).read_text()

    asyncio.run(scenario())
