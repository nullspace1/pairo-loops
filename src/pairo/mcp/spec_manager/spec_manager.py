"""Role-aware MCP workflow for directing, implementing, and verifying tasks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import re
from typing import Annotated, Literal, Mapping, Sequence, TypeVar
from uuid import UUID, uuid4

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel, ConfigDict, Field, field_validator

from pairo.core.agent import JsonObject, SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.clients.client import MCP_AGENT_ID_HEADER
from pairo.core.mcp.mcp_http_wrapper import MCPHttpWrapper


TaskStatus = Literal["pending", "working", "verifying", "accepted"]
PairStatus = Literal["idle", "working", "verifying"]
_WaitResult = TypeVar("_WaitResult")
_TASK_PATTERN = re.compile(
    r"^(?P<name>[^:\r\n]+): (?P<description>.*?) "
    r"\[acceptance: (?P<acceptance>.*?)\] \[status: (?P<status>pending|working|verifying|accepted)\]$"
)


class AcceptanceEvidence(BaseModel):
    """Concise evidence that one acceptance criterion is satisfied."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion: Annotated[str, Field(min_length=1, max_length=1_000)]
    evidence: Annotated[str, Field(min_length=1, max_length=2_000)]

    @field_validator("criterion", "evidence")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Evidence text cannot be blank.")
        return normalized


class TestRunSummary(BaseModel):
    """Bounded test evidence without verbose command output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command: Annotated[str, Field(min_length=1, max_length=1_000)]
    outcome: Literal["passed", "failed", "partial"]
    passed: Annotated[int, Field(ge=0)] = 0
    failed: Annotated[int, Field(ge=0)] = 0
    skipped: Annotated[int, Field(ge=0)] = 0
    duration_seconds: Annotated[float, Field(ge=0)]
    summary: Annotated[str, Field(min_length=1, max_length=2_000)]

    @field_validator("command", "summary")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Test evidence text cannot be blank.")
        return normalized


class VerificationEvidence(BaseModel):
    """Implementation evidence transferred from worker to verifier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    implementation_summary: Annotated[str, Field(min_length=1, max_length=4_000)]
    changed_files: Annotated[list[str], Field(min_length=1, max_length=100)]
    acceptance_evidence: Annotated[
        list[AcceptanceEvidence], Field(min_length=1, max_length=100)
    ]
    test_runs: Annotated[list[TestRunSummary], Field(min_length=1, max_length=50)]
    remaining_risks: Annotated[list[str], Field(max_length=50)] = Field(
        default_factory=list
    )

    @field_validator("implementation_summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Implementation summary cannot be blank.")
        return normalized

    @field_validator("changed_files", "remaining_risks")
    @classmethod
    def normalize_list(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("Evidence lists cannot contain blank values.")
        return normalized


@dataclass(frozen=True, slots=True)
class WorkerVerifierPair:
    """A stable worker/verifier pairing exposed to the director by ``pair_id``."""

    pair_id: str
    worker: Agent[SessionIdentifier]
    verifier: Agent[SessionIdentifier]

    def __post_init__(self) -> None:
        normalized = self.pair_id.strip()
        if not normalized:
            raise ValueError("Worker/verifier pair ID cannot be empty.")
        object.__setattr__(self, "pair_id", normalized)


@dataclass(frozen=True, slots=True)
class _Task:
    name: str
    description: str
    acceptance_criteria: str
    status: TaskStatus

    def line(self) -> str:
        return (
            f"{self.name}: {self.description} "
            f"[acceptance: {self.acceptance_criteria}] [status: {self.status}]"
        )


@dataclass(slots=True)
class _Assignment:
    task_name: str
    completion: asyncio.Future[str]
    status: PairStatus = "working"
    review_result: asyncio.Future[str | None] | None = None
    evidence: VerificationEvidence | None = None


class _FeatureStore:
    """Parse and atomically update task lines without disturbing other content."""

    def __init__(self, folder: Path) -> None:
        self.path = folder / "FEATURES.md"

    def tasks(self) -> dict[str, _Task]:
        if not self.path.exists():
            return {}
        found: dict[str, _Task] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            match = _TASK_PATTERN.fullmatch(line)
            if match:
                values = match.groupdict()
                found[values["name"]] = _Task(
                    values["name"],
                    values["description"],
                    values["acceptance"],
                    values["status"],  # type: ignore[arg-type]
                )
        return found

    def add(self, task: _Task) -> None:
        if task.name in self.tasks():
            raise ValueError(f"Task '{task.name}' already exists.")
        existing = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        separator = "" if not existing or existing.endswith("\n") else "\n"
        self._write(f"{existing}{separator}{task.line()}\n")

    def set_status(self, task_name: str, status: TaskStatus) -> _Task:
        if not self.path.exists():
            raise KeyError(f"Task '{task_name}' does not exist.")
        lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        updated: _Task | None = None
        for index, original in enumerate(lines):
            ending = "\n" if original.endswith("\n") else ""
            match = _TASK_PATTERN.fullmatch(original.rstrip("\r\n"))
            if match and match.group("name") == task_name:
                updated = _Task(
                    task_name,
                    match.group("description"),
                    match.group("acceptance"),
                    status,
                )
                lines[index] = updated.line() + ending
                break
        if updated is None:
            raise KeyError(f"Task '{task_name}' does not exist.")
        self._write("".join(lines))
        return updated

    def replace(self, task: _Task) -> None:
        """Replace one existing task line while preserving unrelated content."""
        self._replace_line(task.name, task.line())

    def delete(self, task_name: str) -> None:
        """Remove one existing task line while preserving unrelated content."""
        self._replace_line(task_name, None)

    def _replace_line(self, task_name: str, replacement: str | None) -> None:
        if not self.path.exists():
            raise KeyError(f"Task '{task_name}' does not exist.")
        lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        for index, original in enumerate(lines):
            match = _TASK_PATTERN.fullmatch(original.rstrip("\r\n"))
            if match and match.group("name") == task_name:
                if replacement is None:
                    del lines[index]
                else:
                    ending = "\n" if original.endswith("\n") else ""
                    lines[index] = replacement + ending
                self._write("".join(lines))
                return
        raise KeyError(f"Task '{task_name}' does not exist.")

    def _write(self, content: str) -> None:
        self.path.parent.mkdir(parents=False, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


class _FeatureHistoryStore:
    """Persist accepted feature evidence for downstream documentation work."""

    _HEADER = "# Feature history\n\n"

    def __init__(self, folder: Path) -> None:
        output_folder = folder / "output"
        output_folder.mkdir(parents=False, exist_ok=True)
        self.path = output_folder / "FEATURE_HISTORY.md"
        if not self.path.exists():
            self._write(self._HEADER)

    def append(self, task: _Task, evidence: VerificationEvidence) -> None:
        existing = self.path.read_text(encoding="utf-8")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry = self._entry(task, evidence, timestamp)
        separator = "" if existing.endswith("\n") else "\n"
        self._write(f"{existing}{separator}{entry}")

    def _entry(
        self,
        task: _Task,
        evidence: VerificationEvidence,
        timestamp: str,
    ) -> str:
        changed_files = "\n".join(f"- `{path}`" for path in evidence.changed_files)
        acceptance_evidence = "\n".join(
            f"- **{item.criterion}** {item.evidence}"
            for item in evidence.acceptance_evidence
        )
        test_runs = "\n".join(self._test_run_line(run) for run in evidence.test_runs)
        risks = (
            "\n".join(f"- {risk}" for risk in evidence.remaining_risks)
            or "- None reported."
        )
        return (
            f"## {task.name}\n\n"
            f"Accepted: {timestamp}\n\n"
            f"{task.description}\n\n"
            f"**Acceptance criteria:** {task.acceptance_criteria}\n\n"
            f"### Implementation\n\n{evidence.implementation_summary}\n\n"
            f"### Changed files\n\n{changed_files}\n\n"
            f"### Acceptance evidence\n\n{acceptance_evidence}\n\n"
            f"### Verification\n\n{test_runs}\n\n"
            f"### Remaining risks\n\n{risks}\n\n"
        )

    def _test_run_line(self, run: TestRunSummary) -> str:
        counts = f"{run.passed} passed, {run.failed} failed, {run.skipped} skipped"
        return (
            f"- `{run.command}` — {run.outcome}; {counts}; "
            f"{run.duration_seconds:g}s. {run.summary}"
        )

    def _write(self, content: str) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


class SpecManager(MCPHttpWrapper):
    """Coordinate task assignments and reviews for registered agents."""

    def __init__(
        self,
        folder: str | Path,
        agents: Mapping[UUID, Agent[SessionIdentifier]],
        director: Agent[SessionIdentifier],
        pairs: Sequence[WorkerVerifierPair],
        port: int = 8082,
        verification_timeout: float = 300,
        documenter: Agent[SessionIdentifier] | None = None,
    ) -> None:
        root = Path(folder).resolve()
        if not root.is_dir():
            raise ValueError("Spec manager folder must be an existing directory.")
        if verification_timeout <= 0:
            raise ValueError("Verification timeout must be greater than zero.")
        self._validate_registration(agents, director, pairs)
        self._validate_documenter_registration(agents, documenter, director, pairs)
        self._director = director
        self._documenter = documenter
        self._pairs = {pair.pair_id: pair for pair in pairs}
        self._worker_pairs = {pair.worker.configuration().id: pair for pair in pairs}
        self._verifier_pairs = {pair.verifier.configuration().id: pair for pair in pairs}
        self._assignments: dict[str, _Assignment] = {}
        self._store = _FeatureStore(root)
        self._history = _FeatureHistoryStore(root)
        self._verification_timeout = verification_timeout
        self._lock = asyncio.Lock()
        super().__init__(port=port)

    @classmethod
    def _validate_documenter_registration(
        cls,
        agents: Mapping[UUID, Agent[SessionIdentifier]],
        documenter: Agent[SessionIdentifier] | None,
        director: Agent[SessionIdentifier],
        pairs: Sequence[WorkerVerifierPair],
    ) -> None:
        if documenter is None:
            return
        configuration = documenter.configuration()
        if agents.get(configuration.id) is not documenter:
            raise ValueError("Documenter is not registered with the agent mapping.")
        role_agents = {director.configuration().id}
        role_agents.update(pair.worker.configuration().id for pair in pairs)
        role_agents.update(pair.verifier.configuration().id for pair in pairs)
        if configuration.id in role_agents:
            raise ValueError("Documenter must not have another spec-manager role.")

    @classmethod
    def _validate_registration(
        cls,
        agents: Mapping[UUID, Agent[SessionIdentifier]],
        director: Agent[SessionIdentifier],
        pairs: Sequence[WorkerVerifierPair],
    ) -> None:
        director_configuration = director.configuration()
        if agents.get(director_configuration.id) is not director:
            raise ValueError("Director is not registered with the agent mapping.")
        pair_ids: set[str] = set()
        participant_ids: set[UUID] = {director_configuration.id}
        for pair in pairs:
            pair_id = pair.pair_id.strip()
            if not pair_id or pair_id in pair_ids:
                raise ValueError("Worker/verifier pair IDs must be non-empty and unique.")
            pair_ids.add(pair_id)
            if pair.worker.configuration().id == pair.verifier.configuration().id:
                raise ValueError(f"Pair '{pair_id}' must use distinct agents.")
            for agent in (pair.worker, pair.verifier):
                configuration = agent.configuration()
                if agents.get(configuration.id) is not agent:
                    raise ValueError(
                        f"Agent '{configuration.name}' is not registered with the agent mapping."
                    )
                if configuration.id in participant_ids:
                    raise ValueError(
                        f"Agent '{configuration.name}' has more than one spec-manager role."
                    )
                participant_ids.add(configuration.id)

    def build_mcp_server(self) -> MCPServer:
        mcp = MCPServer("spec_manager", "Spec Manager", "Coordinate feature tasks and reviews.")

        @mcp.tool("create_task", "Create a pending task in FEATURES.md.")
        async def create_task(
            name: str, description: str, acceptance_criteria: str, ctx: Context
        ) -> str:
            error = self._require_role(ctx, "director")
            if error:
                return error
            try:
                task = _Task(
                    self._field("name", name, forbid_colon=True),
                    self._field("description", description),
                    self._field("acceptance criteria", acceptance_criteria),
                    "pending",
                )
                async with self._lock:
                    self._store.add(task)
                return f"Created task '{task.name}'."
            except (ValueError, OSError) as error_value:
                return f"Could not create task: {error_value}"

        @mcp.tool("modify_task", "Update a pending task's description and acceptance criteria.")
        async def modify_task(
            task_name: str, description: str, acceptance_criteria: str, ctx: Context
        ) -> str:
            error = self._require_role(ctx, "director")
            if error:
                return error
            try:
                updated_task = _Task(
                    self._field("name", task_name, forbid_colon=True),
                    self._field("description", description),
                    self._field("acceptance criteria", acceptance_criteria),
                    "pending",
                )
                async with self._lock:
                    existing_task = self._store.tasks().get(updated_task.name)
                    if existing_task is None:
                        return f"Task '{updated_task.name}' does not exist."
                    if existing_task.status != "pending":
                        return f"Task '{updated_task.name}' is {existing_task.status}, not pending."
                    self._store.replace(updated_task)
                return f"Modified task '{updated_task.name}'."
            except (ValueError, OSError) as error_value:
                return f"Could not modify task: {error_value}"

        @mcp.tool("delete_task", "Delete a pending task from FEATURES.md.")
        async def delete_task(task_name: str, ctx: Context) -> str:
            error = self._require_role(ctx, "director")
            if error:
                return error
            try:
                normalized_name = self._field("name", task_name, forbid_colon=True)
                async with self._lock:
                    task = self._store.tasks().get(normalized_name)
                    if task is None:
                        return f"Task '{normalized_name}' does not exist."
                    if task.status != "pending":
                        return f"Task '{normalized_name}' is {task.status}, not pending."
                    self._store.delete(normalized_name)
                return f"Deleted task '{normalized_name}'."
            except (ValueError, OSError) as error_value:
                return f"Could not delete task: {error_value}"

        @mcp.tool("list_workers", "List worker/verifier pairs and their status.")
        async def list_workers(ctx: Context) -> list[JsonObject] | str:
            error = self._require_role(ctx, "director")
            if error:
                return error
            async with self._lock:
                return [self._pair_summary(pair) for pair in self._pairs.values()]

        @mcp.tool("assign_task", "Assign a pending task to an idle pair.")
        async def assign_task(pair_id: str, task_name: str, ctx: Context) -> str:
            error = self._require_role(ctx, "director")
            if error:
                return error
            return await self._assign(pair_id, task_name)

        @mcp.tool("sleep_on_task", "Wait until an assigned task is accepted.")
        async def sleep_on_task(task_name: str, ctx: Context) -> str:
            error = self._require_role(ctx, "director")
            if error:
                return error
            return await self._sleep_on_task(task_name)

        @mcp.tool(
            "verify_task",
            "Submit concise implementation, acceptance, and test evidence for verification.",
        )
        async def verify_task(evidence: VerificationEvidence, ctx: Context) -> str:
            worker_id = self._agent_id(ctx)
            pair = self._worker_pairs.get(worker_id) if worker_id else None
            if pair is None:
                return "Only a registered worker may verify a task."
            return await self._start_verification(pair, evidence)

        @mcp.tool("reject_task", "Reject the active task with feedback.")
        async def reject_task(message: str, ctx: Context) -> str:
            verifier_id = self._agent_id(ctx)
            pair = self._verifier_pairs.get(verifier_id) if verifier_id else None
            if pair is None:
                return "Only a registered verifier may reject a task."
            if not message.strip():
                return "Rejection message cannot be empty."
            return await self._decide(pair, accepted=False, feedback=message.strip())

        @mcp.tool("accept_task", "Accept the active task.")
        async def accept_task(ctx: Context) -> str:
            verifier_id = self._agent_id(ctx)
            pair = self._verifier_pairs.get(verifier_id) if verifier_id else None
            if pair is None:
                return "Only a registered verifier may accept a task."
            return await self._decide(pair, accepted=True)

        return mcp

    async def _assign(self, pair_id: str, task_name: str) -> str:
        async with self._lock:
            pair = self._pairs.get(pair_id)
            if pair is None:
                return f"Pair '{pair_id}' does not exist."
            if pair_id in self._assignments:
                return f"Pair '{pair_id}' is busy."
            task = self._store.tasks().get(task_name)
            if task is None:
                return f"Task '{task_name}' does not exist."
            if task.status != "pending":
                return f"Task '{task_name}' is {task.status}, not pending."
            try:
                await pair.worker.new_session(
                    self._session_name("work", pair_id, task.name)
                )
                await pair.verifier.new_session(
                    self._session_name("verify", pair_id, task.name)
                )
                await pair.worker.send_message(
                    self._worker_message(task),
                    source=self._director.configuration().name,
                )
                self._store.set_status(task.name, "working")
            except Exception as error:
                return f"Could not assign task '{task.name}': {error}"
            self._assignments[pair_id] = _Assignment(
                task.name,
                asyncio.get_running_loop().create_future(),
            )
            return f"Assigned task '{task.name}' to pair '{pair_id}'."

    async def _sleep_on_task(self, task_name: str) -> str:
        async with self._lock:
            task = self._store.tasks().get(task_name)
            if task is None:
                return f"Task '{task_name}' does not exist."
            if task.status == "accepted":
                return f"Task '{task_name}' is already accepted."
            assignment = next(
                (
                    candidate
                    for candidate in self._assignments.values()
                    if candidate.task_name == task_name
                ),
                None,
            )
            if assignment is None:
                return f"Task '{task_name}' is not assigned."
            completion = assignment.completion
        return await asyncio.shield(completion)

    async def _start_verification(
        self,
        pair: WorkerVerifierPair,
        evidence: VerificationEvidence,
    ) -> str:
        async with self._lock:
            assignment = self._assignments.get(pair.pair_id)
            if assignment is None or assignment.status != "working":
                return f"Pair '{pair.pair_id}' has no working task to verify."
            task = self._store.tasks()[assignment.task_name]
            future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
            try:
                await pair.verifier.send_message(
                    self._verifier_message(task, evidence),
                    source=pair.worker.configuration().name,
                )
                self._store.set_status(task.name, "verifying")
            except Exception as error:
                return f"Could not start verification: {error}"
            assignment.status = "verifying"
            assignment.review_result = future
            assignment.evidence = evidence
        try:
            result = await asyncio.wait_for(
                asyncio.shield(future), timeout=self._verification_timeout
            )
        except asyncio.TimeoutError:
            return (
                f"Verification timed out after {self._verification_timeout:g} seconds; "
                "the verifier may still decide this task."
            )
        return result or "Task accepted."

    async def _decide(
        self, pair: WorkerVerifierPair, *, accepted: bool, feedback: str | None = None
    ) -> str:
        async with self._lock:
            assignment = self._assignments.get(pair.pair_id)
            if assignment is None or assignment.status != "verifying":
                return f"Pair '{pair.pair_id}' has no active verification."
            future = assignment.review_result
            task_name = assignment.task_name
            if accepted and assignment.evidence is None:
                return "Could not record verification decision: evidence is missing."
            try:
                task = self._store.set_status(
                    task_name, "accepted" if accepted else "working"
                )
                if accepted:
                    assert assignment.evidence is not None
                    self._history.append(task, assignment.evidence)
                if not accepted:
                    await pair.worker.send_message(
                        f"Task '{task_name}' was rejected by "
                        f"{pair.verifier.configuration().name}: {feedback}",
                        source=pair.verifier.configuration().name,
                    )
            except Exception as error:
                return f"Could not record verification decision: {error}"
            if accepted:
                del self._assignments[pair.pair_id]
                if not assignment.completion.done():
                    assignment.completion.set_result(f"Accepted task '{task_name}'.")
            else:
                assignment.status = "working"
                assignment.review_result = None
                assignment.evidence = None
            if future is not None and not future.done():
                future.set_result(None if accepted else feedback)

        outcome = f"Accepted task '{task_name}'." if accepted else f"Rejected task '{task_name}'."
        if accepted and self._documenter is not None:
            try:
                await self._documenter.send_message(
                    f"Feature history updated: {self._history.path}",
                    source="spec_manager",
                )
            except Exception as error:
                outcome += f" Documenter notification failed: {error}"
        return outcome

    async def close(self) -> None:
        """Release task waiters before asking HTTP to drain active requests.

        Streamable HTTP shutdown waits for active tool requests.  Some of those
        requests await an assignment-owned future, so stopping the transport
        first would make each side wait for the other indefinitely.  Cancelling
        the futures leaves the assignment and its task file unchanged while the
        request handlers unwind.
        """
        await self._cancel_assignment_waiters()
        await super().close()

    async def _cancel_assignment_waiters(self) -> None:
        """Cancel every outstanding director or worker wait for an assignment."""
        async with self._lock:
            for assignment in self._assignments.values():
                self._cancel_waiter(assignment.completion)
                if assignment.review_result is not None:
                    self._cancel_waiter(assignment.review_result)

    def _cancel_waiter(self, waiter: asyncio.Future[_WaitResult]) -> None:
        if not waiter.done():
            waiter.cancel()

    def _pair_summary(self, pair: WorkerVerifierPair) -> JsonObject:
        assignment = self._assignments.get(pair.pair_id)
        worker = pair.worker.configuration()
        verifier = pair.verifier.configuration()
        return {
            "pair_id": pair.pair_id,
            "worker": {"id": str(worker.id), "name": worker.name},
            "verifier": {"id": str(verifier.id), "name": verifier.name},
            "status": assignment.status if assignment else "idle",
            "task": assignment.task_name if assignment else None,
        }

    def _require_role(self, ctx: Context, role: str) -> str | None:
        agent_id = self._agent_id(ctx)
        allowed = role == "director" and agent_id == self._director.configuration().id
        return None if allowed else f"Only the registered {role} may use this tool."

    def _agent_id(self, ctx: Context) -> UUID | None:
        value = ctx.headers.get(MCP_AGENT_ID_HEADER) if ctx.headers else None
        try:
            return UUID(value) if value else None
        except ValueError:
            return None

    def _field(self, label: str, value: str, *, forbid_colon: bool = False) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"Task {label} cannot be empty.")
        if any(character in normalized for character in "\r\n[]") or (
            forbid_colon and ":" in normalized
        ):
            raise ValueError(f"Task {label} contains a reserved delimiter.")
        return normalized

    def _session_name(self, kind: str, pair_id: str, task_name: str) -> str:
        return f"spec-manager-{kind}-{pair_id}-{task_name}-{uuid4().hex[:8]}"

    def _worker_message(self, task: _Task) -> str:
        return (
            f"Implement task '{task.name}'.\nDescription: {task.description}\n"
            f"Acceptance criteria: {task.acceptance_criteria}\n"
            "When implementation is ready, call spec_manager.verify_task with "
            "a concise implementation summary, changed files, evidence for every "
            "acceptance criterion, summarized test runs, and remaining risks."
        )

    def _verifier_message(
        self,
        task: _Task,
        evidence: VerificationEvidence,
    ) -> str:
        return (
            f"Verify task '{task.name}'.\nDescription: {task.description}\n"
            f"Acceptance criteria: {task.acceptance_criteria}\n"
            "Worker evidence:\n"
            f"{evidence.model_dump_json(indent=2)}\n"
            "Use this handoff to target your review. Independently inspect the "
            "changed behavior and rerun the checks material to your decision.\n"
            "Call spec_manager.accept_task or spec_manager.reject_task with actionable feedback."
        )


_ROLE_INSTRUCTIONS = {
    "director": "Use the spec_manager tools to create, modify, delete, and assign feature tasks; only change or delete pending tasks, and wait on assigned tasks when needed.",
    "worker": "Implement your assigned task, then call spec_manager.verify_task with structured acceptance evidence and summarized test runs; do not submit raw test output. Respond to rejections.",
    "verifier": "Review every acceptance criterion using the worker's structured evidence, independently check material claims, and call spec_manager.accept_task or reject_task exactly once.",
}


class SpecManagerInstaller:
    """Create a manager and configure every participant for its role."""

    def __call__(
        self,
        folder: str | Path,
        agents: Mapping[UUID, Agent[SessionIdentifier]],
        director: Agent[SessionIdentifier],
        pairs: Sequence[WorkerVerifierPair],
        port: int = 8082,
        verification_timeout: float = 300,
        documenter: Agent[SessionIdentifier] | None = None,
    ) -> SpecManager:
        manager = SpecManager(
            folder,
            agents,
            director,
            pairs,
            port,
            verification_timeout,
            documenter,
        )
        participants = [(director, "director")]
        participants.extend((pair.worker, "worker") for pair in pairs)
        participants.extend((pair.verifier, "verifier") for pair in pairs)
        server = manager.as_http_mcp_server()
        skills_root = Path(__file__).with_name("spec_manager_skills")

        for agent, role in participants:
            configuration = agent.configuration()
            configured = next(
                (item for item in configuration.mcp_servers if item.name == server.name),
                None,
            )
            if configured is not None and configured != server:
                raise ValueError(
                    f"Agent '{configuration.name}' already has a different MCP server "
                    f"named '{server.name}'."
                )
        for agent, role in participants:
            configuration = agent.configuration()
            if server not in configuration.mcp_servers:
                configuration.add_mcp_server(server)
            role_skills = skills_root / role
            if role_skills not in configuration.skill_folders:
                configuration.add_skill_folder(role_skills)
            instructions = _ROLE_INSTRUCTIONS[role]
            if instructions not in (configuration.developer_instructions or ""):
                configuration.append_developer_instructions(instructions)
        return manager


install_spec_manager = SpecManagerInstaller()


__all__ = [
    "AcceptanceEvidence",
    "SpecManager",
    "SpecManagerInstaller",
    "TestRunSummary",
    "VerificationEvidence",
    "WorkerVerifierPair",
    "install_spec_manager",
]

