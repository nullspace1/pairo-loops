"""Role-aware MCP workflow for directing, implementing, and verifying tasks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import os
import re
from contextlib import suppress
from typing import Literal, Sequence
from uuid import UUID, uuid4

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from pairo.core.agent import Agent, Runner
from pairo.core.clients.client import MCP_AGENT_ID_HEADER
from pairo.core.mcp.mcp_http_wrapper import MCPHttpWrapper


TaskStatus = Literal["pending", "working", "verifying", "accepted"]
PairStatus = Literal["idle", "working", "verifying"]
_TASK_PATTERN = re.compile(
    r"^(?P<name>[^:\r\n]+): (?P<description>.*?) "
    r"\[acceptance: (?P<acceptance>.*?)\] \[status: (?P<status>pending|working|verifying|accepted)\]$"
)


@dataclass(frozen=True, slots=True)
class WorkerVerifierPair:
    """A stable worker/verifier pairing exposed to the director by ``pair_id``."""

    pair_id: str
    worker: Agent
    verifier: Agent

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
    status: PairStatus = "working"
    review_result: asyncio.Future[str | None] | None = None
    verifier_session: str | None = None


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

    def _write(self, content: str) -> None:
        self.path.parent.mkdir(parents=False, exist_ok=True)
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
        runner: Runner,
        director: Agent,
        pairs: Sequence[WorkerVerifierPair],
        port: int = 8082,
        verification_timeout: float = 300,
    ) -> None:
        root = Path(folder).resolve()
        if not root.is_dir():
            raise ValueError("Spec manager folder must be an existing directory.")
        if verification_timeout <= 0:
            raise ValueError("Verification timeout must be greater than zero.")
        self._validate_registration(runner, director, pairs)
        self._runner = runner
        self._director = director
        self._pairs = {pair.pair_id: pair for pair in pairs}
        self._worker_pairs = {pair.worker.id: pair for pair in pairs}
        self._verifier_pairs = {pair.verifier.id: pair for pair in pairs}
        self._assignments: dict[str, _Assignment] = {}
        self._store = _FeatureStore(root)
        self._verification_timeout = verification_timeout
        self._lock = asyncio.Lock()
        self._cleanup_tasks: set[asyncio.Task[None]] = set()
        super().__init__(port=port)

    @staticmethod
    def _validate_registration(
        runner: Runner, director: Agent, pairs: Sequence[WorkerVerifierPair]
    ) -> None:
        if runner.agents.get(director.id) is not director:
            raise ValueError("Director is not registered with the runner.")
        pair_ids: set[str] = set()
        participant_ids: set[UUID] = {director.id}
        for pair in pairs:
            pair_id = pair.pair_id.strip()
            if not pair_id or pair_id in pair_ids:
                raise ValueError("Worker/verifier pair IDs must be non-empty and unique.")
            pair_ids.add(pair_id)
            if pair.worker.id == pair.verifier.id:
                raise ValueError(f"Pair '{pair_id}' must use distinct agents.")
            for agent in (pair.worker, pair.verifier):
                if runner.agents.get(agent.id) is not agent:
                    raise ValueError(f"Agent '{agent.name}' is not registered with the runner.")
                if agent.id in participant_ids:
                    raise ValueError(f"Agent '{agent.name}' has more than one spec-manager role.")
                participant_ids.add(agent.id)

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

        @mcp.tool("list_workers", "List worker/verifier pairs and their status.")
        async def list_workers(ctx: Context) -> list[dict[str, object]] | str:
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

        @mcp.tool("verify_task", "Submit the calling worker's task for verification.")
        async def verify_task(ctx: Context) -> str:
            worker_id = self._agent_id(ctx)
            pair = self._worker_pairs.get(worker_id) if worker_id else None
            if pair is None:
                return "Only a registered worker may verify a task."
            return await self._start_verification(pair)

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
            session = self._session_name("work", pair_id, task.name)
            try:
                await self._runner.create_session(pair.worker.id, session)
                await self._runner.send_message(
                    pair.worker.id, self._worker_message(task), source=self._director.name
                )
                self._store.set_status(task.name, "working")
            except Exception as error:
                if session in self._runner.session_names(pair.worker.id):
                    await self._safe_delete(pair.worker.id, session)
                return f"Could not assign task '{task.name}': {error}"
            self._assignments[pair_id] = _Assignment(task.name)
            return f"Assigned task '{task.name}' to pair '{pair_id}'."

    async def _start_verification(self, pair: WorkerVerifierPair) -> str:
        async with self._lock:
            assignment = self._assignments.get(pair.pair_id)
            if assignment is None or assignment.status != "working":
                return f"Pair '{pair.pair_id}' has no working task to verify."
            task = self._store.tasks()[assignment.task_name]
            session = self._session_name("verify", pair.pair_id, task.name)
            future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
            try:
                await self._runner.create_session(pair.verifier.id, session)
                await self._runner.send_message(
                    pair.verifier.id, self._verifier_message(task), source=pair.worker.name
                )
                self._store.set_status(task.name, "verifying")
            except Exception as error:
                if session in self._runner.session_names(pair.verifier.id):
                    await self._safe_delete(pair.verifier.id, session)
                return f"Could not start verification: {error}"
            assignment.status = "verifying"
            assignment.review_result = future
            assignment.verifier_session = session
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
            session = assignment.verifier_session
            task_name = assignment.task_name
            try:
                self._store.set_status(task_name, "accepted" if accepted else "working")
                if not accepted:
                    await self._runner.send_message(
                        pair.worker.id,
                        f"Task '{task_name}' was rejected by {pair.verifier.name}: {feedback}",
                        source=pair.verifier.name,
                    )
            except Exception as error:
                return f"Could not record verification decision: {error}"
            if accepted:
                del self._assignments[pair.pair_id]
            else:
                assignment.status = "working"
                assignment.review_result = None
                assignment.verifier_session = None
            if future is not None and not future.done():
                future.set_result(None if accepted else feedback)

        outcome = f"Accepted task '{task_name}'." if accepted else f"Rejected task '{task_name}'."
        if session is not None:
            # MCP decisions normally occur inside the verifier's active turn.
            # Deleting that selected session must wait until the turn completes.
            if self._runner.is_generating(pair.verifier.id):
                cleanup = asyncio.create_task(
                    self._delete_after_verifier_turn(pair.verifier.id, session),
                    name=f"spec-manager-cleanup-{pair.pair_id}",
                )
                self._cleanup_tasks.add(cleanup)
                cleanup.add_done_callback(self._cleanup_tasks.discard)
            else:
                try:
                    await self._runner.delete_session(pair.verifier.id, session)
                except Exception as error:
                    return f"{outcome} Verifier session cleanup failed: {error}"
        return outcome

    async def _delete_after_verifier_turn(self, verifier_id: UUID, session: str) -> None:
        with suppress(Exception):
            await self._runner.wait_for_message(verifier_id)
            await self._runner.delete_session(verifier_id, session)

    async def close(self) -> None:
        await super().close()
        tasks = tuple(self._cleanup_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_delete(self, agent_id: UUID, session: str) -> None:
        try:
            await self._runner.delete_session(agent_id, session)
        except Exception:
            pass

    def _pair_summary(self, pair: WorkerVerifierPair) -> dict[str, object]:
        assignment = self._assignments.get(pair.pair_id)
        return {
            "pair_id": pair.pair_id,
            "worker": {"id": str(pair.worker.id), "name": pair.worker.name},
            "verifier": {"id": str(pair.verifier.id), "name": pair.verifier.name},
            "status": assignment.status if assignment else "idle",
            "task": assignment.task_name if assignment else None,
        }

    def _require_role(self, ctx: Context, role: str) -> str | None:
        agent_id = self._agent_id(ctx)
        allowed = role == "director" and agent_id == self._director.id
        return None if allowed else f"Only the registered {role} may use this tool."

    @staticmethod
    def _agent_id(ctx: Context) -> UUID | None:
        value = ctx.headers.get(MCP_AGENT_ID_HEADER) if ctx.headers else None
        try:
            return UUID(value) if value else None
        except ValueError:
            return None

    @staticmethod
    def _field(label: str, value: str, *, forbid_colon: bool = False) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"Task {label} cannot be empty.")
        if any(character in normalized for character in "\r\n[]") or (
            forbid_colon and ":" in normalized
        ):
            raise ValueError(f"Task {label} contains a reserved delimiter.")
        return normalized

    @staticmethod
    def _session_name(kind: str, pair_id: str, task_name: str) -> str:
        return f"spec-manager-{kind}-{pair_id}-{task_name}-{uuid4().hex[:8]}"

    @staticmethod
    def _worker_message(task: _Task) -> str:
        return (
            f"Implement task '{task.name}'.\nDescription: {task.description}\n"
            f"Acceptance criteria: {task.acceptance_criteria}\n"
            "When implementation is ready, call spec_manager.verify_task."
        )

    @staticmethod
    def _verifier_message(task: _Task) -> str:
        return (
            f"Verify task '{task.name}'.\nDescription: {task.description}\n"
            f"Acceptance criteria: {task.acceptance_criteria}\n"
            "Call spec_manager.accept_task or spec_manager.reject_task with actionable feedback."
        )


_ROLE_INSTRUCTIONS = {
    "director": "Use the spec_manager tools to create, list, and assign feature tasks.",
    "worker": "Implement your assigned task, then call spec_manager.verify_task and respond to rejections.",
    "verifier": "Review the active task and call spec_manager.accept_task or reject_task exactly once.",
}


def install_spec_manager(
    folder: str | Path,
    runner: Runner,
    director: Agent,
    pairs: Sequence[WorkerVerifierPair],
    port: int = 8082,
    verification_timeout: float = 300,
) -> SpecManager:
    """Create and install a role-configured spec manager on all participants."""
    manager = SpecManager(folder, runner, director, pairs, port, verification_timeout)
    participants = [(director, "director")]
    participants.extend((pair.worker, "worker") for pair in pairs)
    participants.extend((pair.verifier, "verifier") for pair in pairs)
    server = manager.as_http_mcp_server()
    skills_root = Path(__file__).with_name("spec_manager_skills")

    # Validation above makes the configuration pass safe from role conflicts.
    for agent, role in participants:
        configured = next((item for item in agent.mcp_servers if item.name == server.name), None)
        if configured is not None and configured != server:
            raise ValueError(
                f"Agent '{agent.name}' already has a different MCP server named '{server.name}'."
            )
    for agent, role in participants:
        if server not in agent.mcp_servers:
            agent.add_mcp_server(server)
        role_skills = skills_root / role
        if role_skills not in agent.skill_folders:
            agent.add_skill_folder(role_skills)
        instructions = _ROLE_INSTRUCTIONS[role]
        if instructions not in (agent.developer_instructions or ""):
            agent.append_developer_instructions(instructions)
    return manager


__all__ = ["SpecManager", "WorkerVerifierPair", "install_spec_manager"]
