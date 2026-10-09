"""Codex SDK implementation of the provider-neutral AgentClient protocol."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, ItemsView, KeysView, ValuesView
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import suppress
from enum import Enum
import json
from pathlib import Path
from shutil import copytree
from threading import Event, Lock
from typing import Literal, cast

from openai_codex.client import CodexClient
from openai_codex.generated.v2_all import (
    AgentMessageDeltaNotification,
    ApprovalsReviewer,
    AskForApproval,
    CommandExecutionOutputDeltaNotification,
    FileChangeOutputDeltaNotification,
    ItemCompletedNotification,
    ItemStartedNotification,
    McpToolCallProgressNotification,
    PlanDeltaNotification,
    ReasoningSummaryTextDeltaNotification,
    ReasoningTextDeltaNotification,
    SandboxMode as CodexSandboxMode,
    ThreadResumeParams,
    ThreadStartParams,
    TurnCompletedNotification,
    TurnStatus,
)
from openai_codex.models import JsonObject as CodexJsonObject
from openai_codex.models import JsonValue as CodexJsonValue
from openai_codex.models import Notification
from pydantic import ConfigDict

from ..agent_config import (
    AgentConfiguration,
    ApprovalMode,
    JsonObject,
    JsonValue,
)
from ..agent_server import MCP_AGENT_NAME_HEADER, SessionIdentifier, SessionNotification
from ..events import (
    ClientActivityCompleted,
    ClientActivityProgress,
    ClientActivityStarted,
    ClientEvent,
    ClientPlanDelta,
    ClientReasoningDelta,
    ClientResponseCompleted,
    ClientResponseInterrupted,
    ClientTextDelta,
)
from ..mcp.servers import HttpMCPServer
from ..permissions import PermissionRequester
from .codex_permissions import (
    translate_permission_decision,
    translate_permission_request,
)


class CodexSessionIdentifier(SessionIdentifier):
    """The only component allowed to understand Codex conversation state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["codex"] = "codex"
    thread_id: str
    turn_id: str | None = None


class _TurnStreamState:
    """Share the latest steered identifier with the blocking reader."""

    def __init__(self, identifier: CodexSessionIdentifier) -> None:
        self._identifier = identifier
        self._lock = Lock()
        self.resume_reading = Event()

    def current(self) -> CodexSessionIdentifier:
        with self._lock:
            return self._identifier

    def update(self, identifier: CodexSessionIdentifier) -> None:
        with self._lock:
            self._identifier = identifier


class CodexAgentClient:
    """Translate neutral operations and models to the synchronous Codex SDK."""

    def __init__(self) -> None:
        self._permission_requester: PermissionRequester | None = None
        self._permission_identifier: CodexSessionIdentifier | None = None
        self._permission_identifier_lock = Lock()
        self._client: CodexClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._close_lock = asyncio.Lock()
        self._notification_executor: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="codex-notifications",
        )
        self._turn_streams: dict[str, _TurnStreamState] = {}

    async def new_session(
        self,
        configuration: AgentConfiguration,
        agent_name: str,
    ) -> SessionIdentifier:
        await self._apply_configuration(configuration)
        client = await self._ensure_client()
        response = await asyncio.to_thread(
            client.thread_start,
            self._thread_start_params(configuration, agent_name),
        )
        return CodexSessionIdentifier(thread_id=response.thread.id)

    async def restore_session(
        self,
        configuration: AgentConfiguration,
        identifier_json: JsonObject,
    ) -> SessionIdentifier:
        await self._apply_configuration(configuration)
        identifier = CodexSessionIdentifier.model_validate(identifier_json)
        client = await self._ensure_client()
        response = await asyncio.to_thread(
            client.thread_resume,
            identifier.thread_id,
            self._thread_resume_params(configuration, identifier.thread_id),
        )
        return identifier.model_copy(
            update={"thread_id": response.thread.id, "turn_id": None}
        )

    async def start(
        self,
        session: SessionIdentifier,
        message: str,
    ) -> SessionIdentifier:
        identifier = self._identifier(session)
        # The SDK can request approval while turn_start is still blocked. Use the
        # persisted pre-turn identifier until Codex returns the new turn ID.
        self._set_permission_identifier(identifier)
        client = await self._ensure_client()
        response = await asyncio.to_thread(
            client.turn_start,
            identifier.thread_id,
            message,
        )
        updated = identifier.model_copy(update={"turn_id": response.turn.id})
        self._set_permission_identifier(updated)
        return updated

    async def steer(
        self,
        session: SessionIdentifier,
        message: str,
    ) -> SessionIdentifier:
        identifier = self._active_identifier(session)
        client = await self._ensure_client()
        response = await asyncio.to_thread(
            client.turn_steer,
            identifier.thread_id,
            identifier.turn_id,
            message,
        )
        updated = identifier.model_copy(update={"turn_id": response.turn_id})
        stream = self._turn_streams.get(identifier.thread_id)
        if stream is not None:
            stream.update(updated)
        self._set_permission_identifier(updated)
        return updated

    async def interrupt(self, session: SessionIdentifier) -> SessionIdentifier:
        identifier = self._active_identifier(session)
        client = await self._ensure_client()
        await asyncio.to_thread(
            client.turn_interrupt,
            identifier.thread_id,
            identifier.turn_id,
        )
        return identifier

    async def notifications(
        self,
        session: SessionIdentifier,
    ) -> AsyncIterator[SessionNotification]:
        identifier = self._active_identifier(session)
        client = await self._ensure_client()
        executor = self._notification_executor
        if executor is None:
            raise RuntimeError("The Codex client is closed.")
        if identifier.thread_id in self._turn_streams:
            raise RuntimeError("A notification stream is already active for this session.")
        stream = _TurnStreamState(identifier)
        self._turn_streams[identifier.thread_id] = stream
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Notification | BaseException] = asyncio.Queue(
            maxsize=128
        )
        stop_requested = Event()

        def enqueue(value: Notification | BaseException) -> bool:
            delivery = asyncio.run_coroutine_threadsafe(queue.put(value), loop)
            while not stop_requested.is_set():
                try:
                    delivery.result(timeout=0.1)
                    return True
                except FutureTimeoutError:
                    continue
            delivery.cancel()
            return False

        def read_turn() -> None:
            try:
                while not stop_requested.is_set():
                    current = stream.current()
                    notification = client.next_turn_notification(current.turn_id)
                    if not enqueue(notification):
                        return
                    if notification.method == "turn/completed":
                        return
                    while not stop_requested.is_set():
                        if stream.resume_reading.wait(timeout=0.1):
                            stream.resume_reading.clear()
                            break
            except BaseException as error:
                enqueue(error)

        reader = loop.run_in_executor(executor, read_turn)
        terminal_received = False
        try:
            while True:
                item = await queue.get()
                if isinstance(item, BaseException):
                    raise item
                current = stream.current()
                event = await self._translate_notification(current, item)
                terminal_received = isinstance(
                    event,
                    (ClientResponseCompleted, ClientResponseInterrupted),
                )
                updated = (
                    current.model_copy(update={"turn_id": None})
                    if terminal_received
                    else current
                )
                stream.update(updated)
                self._set_permission_identifier(None if terminal_received else updated)
                yield SessionNotification(session=updated, event=event)
                if terminal_received:
                    return
                stream.resume_reading.set()
        finally:
            stop_requested.set()
            stream.resume_reading.set()
            if not terminal_received and not reader.done():
                current = stream.current()
                with suppress(Exception):
                    await asyncio.shield(
                        asyncio.to_thread(
                            client.turn_interrupt,
                            current.thread_id,
                            current.turn_id,
                        )
                    )
            with suppress(asyncio.CancelledError, Exception):
                await reader
            if self._turn_streams.get(identifier.thread_id) is stream:
                self._turn_streams.pop(identifier.thread_id, None)

    async def close(self, session: SessionIdentifier) -> None:
        self._identifier(session)
        async with self._close_lock:
            if self._client is not None:
                client, self._client = self._client, None
                await asyncio.to_thread(client.close)
            self._set_permission_identifier(None)
            if self._notification_executor is not None:
                executor, self._notification_executor = (
                    self._notification_executor,
                    None,
                )
                await asyncio.to_thread(
                    executor.shutdown,
                    wait=True,
                    cancel_futures=True,
                )

    async def _ensure_client(self) -> CodexClient:
        if self._client is not None:
            return self._client
        self._loop = asyncio.get_running_loop()
        client = CodexClient(
            approval_handler=(
                self._route_permission
                if self._permission_requester is not None
                else None
            )
        )
        await asyncio.to_thread(client.start)
        await asyncio.to_thread(client.initialize)
        self._client = client
        return client

    def _route_permission(
        self,
        method: str,
        params: CodexJsonObject | None,
    ) -> CodexJsonObject:
        requester = self._permission_requester
        identifier = self._get_permission_identifier()
        if requester is None or self._loop is None or identifier is None:
            return {"decision": "decline"}
        request = translate_permission_request(
            method,
            params,
            identifier.model_dump(mode="json"),
        )
        if request is None:
            return {"decision": "decline"}
        future = asyncio.run_coroutine_threadsafe(
            requester.request(request),
            self._loop,
        )
        return translate_permission_decision(request, future.result())

    async def _apply_configuration(self, configuration: AgentConfiguration) -> None:
        requester = configuration.permission_requester
        if self._client is not None and requester is not self._permission_requester:
            raise RuntimeError(
                "A running Codex client cannot change its permission requester."
            )
        self._permission_requester = requester
        if configuration.skill_folder is not None:
            await asyncio.to_thread(
                self._install_skills,
                configuration.skill_folder,
                configuration.workspace,
            )

    @staticmethod
    def _install_skills(skill_folder: Path, workspace: Path) -> None:
        """Copy project skills into Codex's workspace-local skill directory."""
        destination = workspace / ".codex" / "skills"
        if destination == skill_folder or destination.is_relative_to(skill_folder):
            raise ValueError("skill_folder cannot contain the destination directory.")
        copytree(skill_folder, destination, dirs_exist_ok=True)

    def _set_permission_identifier(
        self,
        identifier: CodexSessionIdentifier | None,
    ) -> None:
        with self._permission_identifier_lock:
            self._permission_identifier = identifier

    def _get_permission_identifier(self) -> CodexSessionIdentifier | None:
        with self._permission_identifier_lock:
            return self._permission_identifier

    async def _translate_notification(
        self,
        identifier: CodexSessionIdentifier,
        notification: Notification,
    ) -> ClientEvent:
        payload = notification.payload
        if isinstance(payload, AgentMessageDeltaNotification):
            return ClientTextDelta(text=str(payload.delta))
        if isinstance(payload, PlanDeltaNotification):
            return ClientPlanDelta(text=str(payload.delta))
        if isinstance(
            payload,
            (ReasoningTextDeltaNotification, ReasoningSummaryTextDeltaNotification),
        ):
            return ClientReasoningDelta(text=str(payload.delta))
        if isinstance(payload, ItemStartedNotification):
            return ClientActivityStarted(activity=self._dump(payload.item))
        if isinstance(payload, ItemCompletedNotification):
            return ClientActivityCompleted(activity=self._dump(payload.item))
        if isinstance(
            payload,
            (
                CommandExecutionOutputDeltaNotification,
                FileChangeOutputDeltaNotification,
                McpToolCallProgressNotification,
            ),
        ):
            return ClientActivityProgress(activity=self._dump(payload))
        if isinstance(payload, TurnCompletedNotification):
            if payload.turn.status is TurnStatus.interrupted:
                return ClientResponseInterrupted()
            return ClientResponseCompleted(
                message=await self._read_final_response(identifier.thread_id)
            )
        return ClientActivityProgress(activity=self._dump(payload))

    async def _read_final_response(self, thread_id: str) -> str:
        client = await self._ensure_client()
        response = await asyncio.to_thread(
            client.thread_read,
            thread_id,
            include_turns=True,
        )
        values = cast(CodexJsonObject, response.model_dump(by_alias=True))
        messages = self._collect_text(values)
        return messages[-1] if messages else "Codex completed without a text response."

    @classmethod
    def _collect_text(cls, value: CodexJsonValue) -> list[str]:
        if isinstance(value, dict):
            text = value.get("text")
            nested = [
                message
                for item in value.values()
                for message in cls._collect_text(item)
            ]
            return ([text] if isinstance(text, str) else []) + nested
        if isinstance(value, list):
            return [
                message
                for item in value
                for message in cls._collect_text(item)
            ]
        return []

    @classmethod
    def _dump(cls, value: object) -> JsonObject:
        """Convert provider activity data to a recursively JSON-safe object."""
        serialized = json.dumps(value, default=cls._json_default)
        dumped = json.loads(serialized)
        if isinstance(dumped, dict):
            return cast(JsonObject, dumped)
        return {"value": cast(JsonValue, dumped)}

    @staticmethod
    def _json_default(value: object) -> object:
        if hasattr(value, "model_dump"):
            return value.model_dump(
                mode="python",
                by_alias=True,
                exclude_none=True,
            )
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (ItemsView, KeysView, ValuesView)):
            return list(value)
        return str(value)

    @staticmethod
    def _identifier(session: SessionIdentifier) -> CodexSessionIdentifier:
        if isinstance(session, CodexSessionIdentifier):
            return session
        return CodexSessionIdentifier.model_validate(session.model_dump(mode="json"))

    @classmethod
    def _active_identifier(
        cls,
        session: SessionIdentifier,
    ) -> CodexSessionIdentifier:
        identifier = cls._identifier(session)
        if identifier.turn_id is None:
            raise ValueError("The Codex session has no active turn.")
        return identifier

    @classmethod
    def _thread_start_params(
        cls,
        config: AgentConfiguration,
        agent_name: str,
    ) -> ThreadStartParams:
        return ThreadStartParams(
            approval_policy=cls._approval_policy(),
            approvals_reviewer=cls._reviewer(config),
            base_instructions=config.base_instructions,
            config=cls._codex_config(config, agent_name=agent_name) or None,
            cwd=str(config.workspace),
            developer_instructions=config.developer_instructions,
            model=config.model,
            sandbox=CodexSandboxMode(config.sandbox.value),
        )

    @classmethod
    def _thread_resume_params(
        cls,
        config: AgentConfiguration,
        thread_id: str,
    ) -> ThreadResumeParams:
        return ThreadResumeParams(
            thread_id=thread_id,
            approval_policy=cls._approval_policy(),
            approvals_reviewer=cls._reviewer(config),
            base_instructions=config.base_instructions,
            config=cls._codex_config(config) or None,
            cwd=str(config.workspace),
            developer_instructions=config.developer_instructions,
            model=config.model,
            sandbox=CodexSandboxMode(config.sandbox.value),
        )

    @staticmethod
    def _reviewer(config: AgentConfiguration) -> ApprovalsReviewer:
        return (
            ApprovalsReviewer.user
            if config.approval_mode is ApprovalMode.USER
            else ApprovalsReviewer.auto_review
        )

    @staticmethod
    def _approval_policy() -> AskForApproval:
        return AskForApproval.model_validate(
            {
                "granular": {
                    "sandbox_approval": True,
                    "rules": True,
                    "mcp_elicitations": True,
                    "request_permissions": True,
                    "skill_approval": True,
                }
            }
        )

    @staticmethod
    def _codex_config(
        config: AgentConfiguration,
        *,
        agent_name: str | None = None,
    ) -> CodexJsonObject:
        result: CodexJsonObject = {}
        if config.reasoning_effort is not None:
            result["model_reasoning_effort"] = config.reasoning_effort.value
        if config.web_search is not None:
            result["web_search"] = config.web_search.value
        if config.mcp_servers:
            servers: CodexJsonObject = {}
            for server in config.mcp_servers:
                connection: CodexJsonObject
                if isinstance(server, HttpMCPServer):
                    connection = {
                        "url": server.url,
                        "startup_timeout_sec": server.startup_timeout_sec,
                    }
                    if server.env:
                        connection["env_http_headers"] = dict(server.env)
                    if agent_name is not None:
                        connection["http_headers"] = {
                            MCP_AGENT_NAME_HEADER: agent_name,
                        }
                else:
                    connection = {
                        "command": server.command,
                        "args": list(server.args),
                        "startup_timeout_sec": server.startup_timeout_sec,
                    }
                    if server.env:
                        connection["env"] = dict(server.env)
                    if agent_name is not None:
                        connection.setdefault("env", {})[MCP_AGENT_NAME_HEADER] = agent_name
                if server.permissions is not None:
                    permissions = server.permissions
                    if permissions.default_tools_approval_mode is not None:
                        connection["default_tools_approval_mode"] = (
                            permissions.default_tools_approval_mode.value
                        )
                    if permissions.tool_approval_modes:
                        connection["tools"] = {
                            name: {"approval_mode": mode.value}
                            for name, mode in permissions.tool_approval_modes.items()
                        }
                servers[server.name] = connection
            result["mcp_servers"] = servers
        if config.hooks:
            result["hooks"] = {
                hook_type.value: [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": hook.command,
                                "async": hook.is_async,
                            }
                            for hook in hooks
                        ]
                    }
                ]
                for hook_type, hooks in config.hooks.items()
            }
        return result


__all__ = ["CodexAgentClient", "CodexSessionIdentifier"]
