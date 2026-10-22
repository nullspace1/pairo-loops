"""Role-aware MCP workflow for directing, implementing, and verifying tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import re
from typing import Annotated, Literal, Mapping, Sequence, TypeVar
from uuid import UUID, uuid4

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pairo.core.agent import JsonObject, SessionIdentifier
from pairo.core.clients import Agent
from pairo.core.clients.client import MCP_AGENT_ID_HEADER
from pairo.core.mcp.mcp_http_wrapper import MCPHttpWrapper


TaskStatus = Literal["pending", "working", "verifying", "accepted"]
PairStatus = Literal["idle", "working", "verifying"]
_WaitResult = TypeVar("_WaitResult")
_FEATURE_HEADER = "# Features\n"
_TASK_BLOCK_PATTERN = re.compile(r"```pairo-task\n(?P<payload>.*?)\n```", re.DOTALL)


class AcceptanceCriterion(BaseModel):
    """One stable, independently verifiable task outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, Field(pattern=r"^AC[1-9][0-9]*$", max_length=20)]
    description: Annotated[str, Field(min_length=1, max_length=2_000)]

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Acceptance criterion description cannot be blank.")
        return normalized


class AcceptanceEvidence(BaseModel):
    """Concise evidence that one acceptance criterion is satisfied."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: Annotated[str, Field(pattern=r"^AC[1-9][0-9]*$", max_length=20)]
    evidence: Annotated[str, Field(min_length=1, max_length=2_000)]
    test_run_ids: Annotated[list[str], Field(max_length=50)] = Field(default_factory=list)
    residual_risk: Annotated[str | None, Field(max_length=2_000)] = None

    @field_validator("evidence")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Evidence text cannot be blank.")
        return normalized

    @field_validator("test_run_ids")
    @classmethod
    def normalize_test_run_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("Test run IDs must be non-empty and unique.")
        return normalized

    @field_validator("residual_risk")
    @classmethod
    def normalize_residual_risk(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class TestRunSummary(BaseModel):
    """Bounded test evidence without verbose command output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, Field(pattern=r"^TR[1-9][0-9]*$", max_length=20)]
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
    correction_resolutions: Annotated[
        list["CorrectionResolution"], Field(max_length=100)
    ] = Field(default_factory=list)
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


class VerificationCorrection(BaseModel):
    """One actionable verifier finding tied to acceptance criteria."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, Field(pattern=r"^C[1-9][0-9]*$", max_length=20)]
    criterion_ids: Annotated[list[str], Field(min_length=1, max_length=100)]
    message: Annotated[str, Field(min_length=1, max_length=2_000)]

    @field_validator("criterion_ids")
    @classmethod
    def normalize_criterion_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("Correction criterion IDs must be non-empty and unique.")
        return normalized

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Correction message cannot be blank.")
        return normalized


class CorrectionResolution(BaseModel):
    """Worker evidence resolving one prior verifier correction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    correction_id: Annotated[str, Field(pattern=r"^C[1-9][0-9]*$", max_length=20)]
    resolution: Annotated[str, Field(min_length=1, max_length=2_000)]

    @field_validator("resolution")
    @classmethod
    def normalize_resolution(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Correction resolution cannot be blank.")
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


class _Task(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    acceptance_criteria: list[AcceptanceCriterion]
    status: TaskStatus

    @model_validator(mode="after")
    def require_unique_criteria(self) -> "_Task":
        criterion_ids = [criterion.id for criterion in self.acceptance_criteria]
        if not criterion_ids:
            raise ValueError("A task must contain at least one acceptance criterion.")
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("Acceptance criterion IDs must be unique.")
        return self


@dataclass(slots=True)
class _Assignment:
    task_name: str
    completion: asyncio.Future[str]
    status: PairStatus = "working"
    review_result: asyncio.Future[str | None] | None = None
    evidence: VerificationEvidence | None = None
    outstanding_corrections: tuple[VerificationCorrection, ...] = ()


class _FeatureStore:
    """Persist tasks in the only supported structured Markdown format."""

    def __init__(self, folder: Path) -> None:
        self.path = folder / "FEATURES.md"

    def tasks(self) -> dict[str, _Task]:
        if not self.path.exists():
            return {}
        content = self.path.read_text(encoding="utf-8")
        matches = tuple(_TASK_BLOCK_PATTERN.finditer(content))
        residue = _TASK_BLOCK_PATTERN.sub("", content).strip()
        if residue != _FEATURE_HEADER.strip():
            raise ValueError(
                "FEATURES.md uses an unsupported format; recreate it with SpecManager."
            )
        found: dict[str, _Task] = {}
        for match in matches:
            task = _Task.model_validate_json(match.group("payload"))
            if task.name in found:
                raise ValueError(f"FEATURES.md contains duplicate task '{task.name}'.")
            found[task.name] = task
        return found

    def add(self, task: _Task) -> None:
        tasks = self.tasks()
        if task.name in tasks:
            raise ValueError(f"Task '{task.name}' already exists.")
        tasks[task.name] = task
        self._write_tasks(tasks.values())

    def set_status(self, task_name: str, status: TaskStatus) -> _Task:
        tasks = self.tasks()
        current = tasks.get(task_name)
        if current is None:
            raise KeyError(f"Task '{task_name}' does not exist.")
        updated = current.model_copy(update={"status": status})
        tasks[task_name] = updated
        self._write_tasks(tasks.values())
        return updated

    def replace(self, task: _Task) -> None:
        tasks = self.tasks()
        if task.name not in tasks:
            raise KeyError(f"Task '{task.name}' does not exist.")
        tasks[task.name] = task
        self._write_tasks(tasks.values())

    def delete(self, task_name: str) -> None:
        tasks = self.tasks()
        if task_name not in tasks:
            raise KeyError(f"Task '{task_name}' does not exist.")
        del tasks[task_name]
        self._write_tasks(tasks.values())

    def _write_tasks(self, tasks: Iterable[_Task]) -> None:
        ordered = list(tasks)
        blocks = "\n\n".join(
            f"```pairo-task\n{task.model_dump_json(indent=2)}\n```" for task in ordered
        )
        suffix = f"\n\n{blocks}\n" if blocks else "\n"
        self._write(f"{_FEATURE_HEADER}{suffix}")

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

    def append(
        self,
        task: _Task,
        evidence: VerificationEvidence,
        corrections: tuple[VerificationCorrection, ...],
    ) -> None:
        existing = self.path.read_text(encoding="utf-8")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry = self._entry(task, evidence, corrections, timestamp)
        separator = "" if existing.endswith("\n") else "\n"
        self._write(f"{existing}{separator}{entry}")

    def _entry(
        self,
        task: _Task,
        evidence: VerificationEvidence,
        corrections: tuple[VerificationCorrection, ...],
        timestamp: str,
    ) -> str:
        changed_files = "\n".join(f"- `{path}`" for path in evidence.changed_files)
        criteria = "\n".join(
            f"- **{criterion.id}:** {criterion.description}"
            for criterion in task.acceptance_criteria
        )
        acceptance_evidence = "\n".join(
            self._acceptance_evidence_line(item) for item in evidence.acceptance_evidence
        )
        resolutions = (
            "\n".join(
                f"- **{item.correction_id}:** {item.resolution}"
                for item in evidence.correction_resolutions
            )
            or "- None required."
        )
        correction_lines = (
            "\n".join(
                f"- **{item.id}** ({', '.join(item.criterion_ids)}): {item.message}"
                for item in corrections
            )
            or "- None required."
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
            f"### Acceptance criteria\n\n{criteria}\n\n"
            f"### Implementation\n\n{evidence.implementation_summary}\n\n"
            f"### Changed files\n\n{changed_files}\n\n"
            f"### Acceptance evidence\n\n{acceptance_evidence}\n\n"
            f"### Verification corrections\n\n{correction_lines}\n\n"
            f"### Correction resolutions\n\n{resolutions}\n\n"
            f"### Verification\n\n{test_runs}\n\n"
            f"### Remaining risks\n\n{risks}\n\n"
        )

    def _acceptance_evidence_line(self, item: AcceptanceEvidence) -> str:
        tests = ", ".join(item.test_run_ids) or "no test run"
        risk = f" Residual risk: {item.residual_risk}" if item.residual_risk else ""
        return f"- **{item.criterion_id}** ({tests}) {item.evidence}{risk}"

    def _test_run_line(self, run: TestRunSummary) -> str:
        counts = f"{run.passed} passed, {run.failed} failed, {run.skipped} skipped"
        return (
            f"- **{run.id}:** `{run.command}` — {run.outcome}; {counts}; "
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
            name: str,
            description: str,
            acceptance_criteria: list[AcceptanceCriterion],
            ctx: Context,
        ) -> str:
            error = self._require_role(ctx, "director")
            if error:
                return error
            try:
                task = _Task(
                    name=self._task_name(name),
                    description=self._text("description", description),
                    acceptance_criteria=acceptance_criteria,
                    status="pending",
                )
                async with self._lock:
                    self._store.add(task)
                return f"Created task '{task.name}'."
            except (ValueError, OSError) as error_value:
                return f"Could not create task: {error_value}"

        @mcp.tool("modify_task", "Update a pending task's description and acceptance criteria.")
        async def modify_task(
            task_name: str,
            description: str,
            acceptance_criteria: list[AcceptanceCriterion],
            ctx: Context,
        ) -> str:
            error = self._require_role(ctx, "director")
            if error:
                return error
            try:
                updated_task = _Task(
                    name=self._task_name(task_name),
                    description=self._text("description", description),
                    acceptance_criteria=acceptance_criteria,
                    status="pending",
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
                normalized_name = self._task_name(task_name)
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

        @mcp.tool("reject_task", "Reject the active task with structured corrections.")
        async def reject_task(
            corrections: list[VerificationCorrection], ctx: Context
        ) -> str:
            verifier_id = self._agent_id(ctx)
            pair = self._verifier_pairs.get(verifier_id) if verifier_id else None
            if pair is None:
                return "Only a registered verifier may reject a task."
            if not corrections:
                return "A rejection must contain at least one correction."
            return await self._decide(
                pair, accepted=False, corrections=tuple(corrections)
            )

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
            evidence_error = self._evidence_error(
                task, evidence, assignment.outstanding_corrections
            )
            if evidence_error is not None:
                return f"Could not start verification: {evidence_error}"
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

    def _evidence_error(
        self,
        task: _Task,
        evidence: VerificationEvidence,
        corrections: tuple[VerificationCorrection, ...],
    ) -> str | None:
        expected_criteria = {criterion.id for criterion in task.acceptance_criteria}
        evidence_criteria = [item.criterion_id for item in evidence.acceptance_evidence]
        if len(evidence_criteria) != len(set(evidence_criteria)):
            return "Acceptance evidence contains duplicate criterion IDs."
        if set(evidence_criteria) != expected_criteria:
            return "Acceptance evidence must map exactly once to every task criterion."
        test_run_ids = [run.id for run in evidence.test_runs]
        if len(test_run_ids) != len(set(test_run_ids)):
            return "Test run IDs must be unique."
        known_runs = set(test_run_ids)
        referenced_runs = {
            run_id
            for item in evidence.acceptance_evidence
            for run_id in item.test_run_ids
        }
        if not referenced_runs.issubset(known_runs):
            return "Acceptance evidence references an unknown test run ID."
        expected_corrections = {item.id for item in corrections}
        resolution_ids = [
            resolution.correction_id for resolution in evidence.correction_resolutions
        ]
        if len(resolution_ids) != len(set(resolution_ids)):
            return "Correction resolutions contain duplicate correction IDs."
        if set(resolution_ids) != expected_corrections:
            return "Correction resolutions must map exactly once to every open correction."
        return None

    def _correction_error(
        self,
        task_name: str,
        corrections: tuple[VerificationCorrection, ...],
        accepted: bool,
    ) -> str | None:
        if accepted:
            return None if not corrections else "Acceptance cannot include corrections."
        if not corrections:
            return "A rejection must contain at least one correction."
        correction_ids = [correction.id for correction in corrections]
        if len(correction_ids) != len(set(correction_ids)):
            return "Correction IDs must be unique."
        task = self._store.tasks()[task_name]
        criterion_ids = {criterion.id for criterion in task.acceptance_criteria}
        referenced = {
            criterion_id
            for correction in corrections
            for criterion_id in correction.criterion_ids
        }
        if not referenced.issubset(criterion_ids):
            return "Corrections reference an unknown acceptance criterion ID."
        return None

    async def _decide(
        self,
        pair: WorkerVerifierPair,
        *,
        accepted: bool,
        corrections: tuple[VerificationCorrection, ...] = (),
    ) -> str:
        async with self._lock:
            assignment = self._assignments.get(pair.pair_id)
            if assignment is None or assignment.status != "verifying":
                return f"Pair '{pair.pair_id}' has no active verification."
            future = assignment.review_result
            task_name = assignment.task_name
            if accepted and assignment.evidence is None:
                return "Could not record verification decision: evidence is missing."
            correction_error = self._correction_error(task_name, corrections, accepted)
            if correction_error is not None:
                return correction_error
            try:
                task = self._store.set_status(
                    task_name, "accepted" if accepted else "working"
                )
                if accepted:
                    assert assignment.evidence is not None
                    self._history.append(
                        task,
                        assignment.evidence,
                        assignment.outstanding_corrections,
                    )
                if not accepted:
                    assignment.outstanding_corrections = corrections
                    await pair.worker.send_message(
                        f"Task '{task_name}' was rejected by "
                        f"{pair.verifier.configuration().name}. Required corrections:\n"
                        + "\n".join(
                            f"- {item.id} ({', '.join(item.criterion_ids)}): {item.message}"
                            for item in corrections
                        ),
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
                future.set_result(
                    None if accepted else f"{len(corrections)} correction(s) required."
                )

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

    def _text(self, label: str, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"Task {label} cannot be empty.")
        return normalized

    def _task_name(self, value: str) -> str:
        normalized = self._text("name", value)
        if any(character in normalized for character in "\r\n"):
            raise ValueError("Task name cannot contain a line break.")
        return normalized

    def _session_name(self, kind: str, pair_id: str, task_name: str) -> str:
        return f"spec-manager-{kind}-{pair_id}-{task_name}-{uuid4().hex[:8]}"

    def _worker_message(self, task: _Task) -> str:
        criteria = "\n".join(
            f"- {criterion.id}: {criterion.description}"
            for criterion in task.acceptance_criteria
        )
        return (
            f"Implement task '{task.name}'.\nDescription: {task.description}\n"
            f"Acceptance criteria:\n{criteria}\n"
            "When implementation is ready, call spec_manager.verify_task with "
            "a concise implementation summary, changed files, evidence for every "
            "acceptance criterion, summarized test runs, and remaining risks."
        )

    def _verifier_message(
        self,
        task: _Task,
        evidence: VerificationEvidence,
    ) -> str:
        criteria = "\n".join(
            f"- {criterion.id}: {criterion.description}"
            for criterion in task.acceptance_criteria
        )
        return (
            f"Verify task '{task.name}'.\nDescription: {task.description}\n"
            f"Acceptance criteria:\n{criteria}\n"
            "Worker evidence:\n"
            f"{evidence.model_dump_json(indent=2)}\n"
            "Use this handoff to target your review. Independently inspect the "
            "changed behavior and rerun the checks material to your decision.\n"
            "Call spec_manager.accept_task or spec_manager.reject_task with actionable feedback."
        )


_ROLE_INSTRUCTIONS = {
    "director": "Use the spec_manager tools to create, modify, delete, and assign feature tasks with stable acceptance-criterion IDs; only change or delete pending tasks. Report delegation state before waiting on an assigned task.",
    "worker": "Implement the assigned task, then call spec_manager.verify_task with exactly one evidence record per criterion ID, referenced test-run IDs, every outstanding correction resolution, and no raw test output.",
    "verifier": "Audit every criterion and prior correction, independently check material claims, then call spec_manager.accept_task once or reject_task once with all structured corrections.",
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
    "AcceptanceCriterion",
    "AcceptanceEvidence",
    "CorrectionResolution",
    "SpecManager",
    "SpecManagerInstaller",
    "TestRunSummary",
    "VerificationEvidence",
    "VerificationCorrection",
    "WorkerVerifierPair",
    "install_spec_manager",
]

