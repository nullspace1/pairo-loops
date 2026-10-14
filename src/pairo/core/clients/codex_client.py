"""Codex SDK implementation of the provider-neutral AgentClient protocol."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, ItemsView, KeysView, ValuesView
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from shutil import copytree
from threading import Event, Lock
from typing import Literal, cast
from uuid import UUID

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
    ThreadTokenUsageUpdatedNotification,
    TokenUsageBreakdown,
    TurnCompletedNotification,
    TurnStatus,
)
from openai_codex.models import JsonObject as CodexJsonObject
from openai_codex.models import JsonValue as CodexJsonValue
from openai_codex.models import Notification
from pydantic import ConfigDict, JsonValue

from ..agent.agent import Agent
from ..agent.approval_mode import ApprovalMode
from ..agent.json_object import JsonObject
from .client import MCP_AGENT_ID_HEADER, SessionIdentifier, SessionNotification
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
    ClientTokenUsageUpdated,
    TokenUsage,
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


@dataclass(frozen=True, slots=True)
class _SessionContext:
    agent_id: UUID
    permission_requester: PermissionRequester | None
    identifier: CodexSessionIdentifier


class CodexAgentClient:
    """Translate neutral operations and models to the synchronous Codex SDK."""

    def __init__(self) -> None:
        self._client: CodexClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client_lock = asyncio.Lock()
        self._closed = False
        self._session_contexts: dict[str, _SessionContext] = {}
        self._session_context_lock = Lock()
        self._turn_streams: dict[str, _TurnStreamState] = {}

    async def new_session(
        self,
        agent: Agent,
    ) -> SessionIdentifier:
        configuration = agent.snapshot()
        await self._apply_configuration(configuration)
        client = await self._ensure_client()
        response = await asyncio.to_thread(
            client.thread_start,
            self._thread_start_params(configuration),
        )
        identifier = CodexSessionIdentifier(thread_id=response.thread.id)
        self._register_session(configuration, identifier)
        return identifier

    async def restore_session(
        self,
        agent: Agent,
        identifier_json: JsonObject,
    ) -> SessionIdentifier:
        configuration = agent.snapshot()
        await self._apply_configuration(configuration)
        identifier = CodexSessionIdentifier.model_validate(identifier_json)
        client = await self._ensure_client()
        response = await asyncio.to_thread(
            client.thread_resume,
            identifier.thread_id,
            self._thread_resume_params(configuration, identifier.thread_id),
        )
        restored = identifier.model_copy(
            update={"thread_id": response.thread.id, "turn_id": None}
        )
        self._register_session(configuration, restored)
        return restored

    async def start(
        self,
        session: SessionIdentifier,
        message: str,
    ) -> SessionIdentifier:
        identifier = self._identifier(session)
        self._require_session_context(identifier.thread_id)
        client = await self._ensure_client()
        response = await asyncio.to_thread(
            client.turn_start,
            identifier.thread_id,
            message,
        )
        updated = identifier.model_copy(update={"turn_id": response.turn.id})
        self._update_session_identifier(updated)
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
            self._turn_id(identifier),
            message,
        )
        updated = identifier.model_copy(update={"turn_id": response.turn_id})
        stream = self._turn_streams.get(identifier.thread_id)
        if stream is not None:
            stream.update(updated)
        self._update_session_identifier(updated)
        return updated

    async def compact(self, session: SessionIdentifier) -> None:
        identifier = self._identifier(session)
        self._require_session_context(identifier.thread_id)
        client = await self._ensure_client()
        await asyncio.to_thread(client.thread_compact, identifier.thread_id)

    async def interrupt(self, session: SessionIdentifier) -> SessionIdentifier:
        identifier = self._active_identifier(session)
        client = await self._ensure_client()
        await asyncio.to_thread(
            client.turn_interrupt,
            identifier.thread_id,
            self._turn_id(identifier),
        )
        return identifier

    async def notifications(
        self,
        session: SessionIdentifier,
    ) -> AsyncIterator[SessionNotification]:
        identifier = self._active_identifier(session)
        client = await self._ensure_client()
        self._require_session_context(identifier.thread_id)
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
                    notification = client.next_turn_notification(
                        self._turn_id(current)
                    )
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

        # Each active turn needs an independent blocking reader. The default
        # executor can grow beyond one worker, unlike the former single-agent
        # executor which serialized every agent's notifications.
        reader = loop.run_in_executor(None, read_turn)
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
                self._update_session_identifier(updated)
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
                            self._turn_id(current),
                        )
                    )
            with suppress(asyncio.CancelledError, Exception):
                await reader
            if self._turn_streams.get(identifier.thread_id) is stream:
                self._turn_streams.pop(identifier.thread_id, None)

    async def close(self) -> None:
        async with self._client_lock:
            if self._closed:
                return
            self._closed = True
            if self._client is not None:
                client, self._client = self._client, None
                await asyncio.to_thread(client.close)
            with self._session_context_lock:
                self._session_contexts.clear()


    async def _ensure_client(self) -> CodexClient:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is not None:
                return self._client
            if self._closed:
                raise RuntimeError("The Codex client is closed.")
            self._loop = asyncio.get_running_loop()
            client = CodexClient(approval_handler=self._route_permission)
            try:
                await asyncio.to_thread(client.start)
                await asyncio.to_thread(client.initialize)
            except BaseException:
                with suppress(Exception):
                    await asyncio.to_thread(client.close)
                raise
            self._client = client
            return client

    def _route_permission(
        self,
        method: str,
        params: CodexJsonObject | None,
    ) -> CodexJsonObject:
        thread_id = self._permission_thread_id(params)
        context = self._session_context(thread_id) if thread_id is not None else None
        if context is None or context.permission_requester is None or self._loop is None:
            return {"decision": "decline"}
        request = translate_permission_request(
            method,
            params,
            context.identifier.model_dump(mode="json"),
        )
        if request is None:
            return {"decision": "decline"}
        future = asyncio.run_coroutine_threadsafe(
            context.permission_requester.request(request),
            self._loop,
        )
        return translate_permission_decision(request, future.result())

    async def _apply_configuration(self, configuration: Agent) -> None:
        for skill_folder in configuration.skill_folders:
            await asyncio.to_thread(
                self._install_skills,
                skill_folder,
                configuration.workspace,
            )

    @staticmethod
    def _install_skills(skill_folder: Path, workspace: Path) -> None:
        """Copy project skills into Codex's workspace-local skill directory."""
        destination = workspace / ".codex" / "skills"
        if destination == skill_folder or destination.is_relative_to(skill_folder):
            raise ValueError("skill_folder cannot contain the destination directory.")
        copytree(skill_folder, destination, dirs_exist_ok=True)

    def _register_session(self, agent: Agent, identifier: CodexSessionIdentifier) -> None:
        context = _SessionContext(
            agent_id=agent.id,
            permission_requester=agent.permission_requester,
            identifier=identifier,
        )
        with self._session_context_lock:
            self._session_contexts[identifier.thread_id] = context

    def _update_session_identifier(self, identifier: CodexSessionIdentifier) -> None:
        with self._session_context_lock:
            current = self._session_contexts.get(identifier.thread_id)
            if current is None:
                raise ValueError(
                    f"Codex session '{identifier.thread_id}' is not registered."
                )
            self._session_contexts[identifier.thread_id] = _SessionContext(
                agent_id=current.agent_id,
                permission_requester=current.permission_requester,
                identifier=identifier,
            )

    def _require_session_context(self, thread_id: str) -> _SessionContext:
        context = self._session_context(thread_id)
        if context is None:
            raise ValueError(f"Codex session '{thread_id}' is not registered.")
        return context

    def _session_context(self, thread_id: str) -> _SessionContext | None:
        with self._session_context_lock:
            return self._session_contexts.get(thread_id)

    @staticmethod
    def _permission_thread_id(params: CodexJsonObject | None) -> str | None:
        if params is None:
            return None
        value = params.get("threadId")
        return value if isinstance(value, str) and value else None

    async def _translate_notification(
        self,
        identifier: CodexSessionIdentifier,
        notification: Notification,
    ) -> ClientEvent:
        payload = notification.payload
        if isinstance(payload, AgentMessageDeltaNotification):
            return ClientTextDelta(text=str(payload.delta))
        if isinstance(payload, ThreadTokenUsageUpdatedNotification):
            return ClientTokenUsageUpdated(
                last=self._token_usage(payload.token_usage.last),
                total=self._token_usage(payload.token_usage.total),
                model_context_window=payload.token_usage.model_context_window,
            )
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

    @staticmethod
    def _token_usage(usage: TokenUsageBreakdown) -> TokenUsage:
        return TokenUsage(
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_output_tokens=usage.reasoning_output_tokens,
            total_tokens=usage.total_tokens,
            cache_write_input_tokens=usage.cache_write_input_tokens,
        )

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
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump(
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

    @staticmethod
    def _turn_id(identifier: CodexSessionIdentifier) -> str:
        if identifier.turn_id is None:
            raise ValueError("The Codex session has no active turn.")
        return identifier.turn_id

    @classmethod
    def _thread_start_params(
        cls,
        config: Agent,
    ) -> ThreadStartParams:
        return ThreadStartParams(
            approval_policy=cls._approval_policy(),
            approvals_reviewer=cls._reviewer(config),
            base_instructions=config.base_instructions,
            config=cls._codex_config(config) or None,
            cwd=str(config.workspace),
            developer_instructions=config.developer_instructions,
            model=config.model,
            sandbox=CodexSandboxMode(config.sandbox.value),
        )

    @classmethod
    def _thread_resume_params(
        cls,
        config: Agent,
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
    def _reviewer(config: Agent) -> ApprovalsReviewer:
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
        config: Agent,
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
                    connection["http_headers"] = {
                        MCP_AGENT_ID_HEADER: str(config.id),
                    }
                else:
                    environment: CodexJsonObject = {}
                    if server.env:
                        environment.update(server.env)
                    environment[MCP_AGENT_ID_HEADER] = str(config.id)
                    connection = {
                        "command": server.command,
                        "args": list(server.args),
                        "startup_timeout_sec": server.startup_timeout_sec,
                        "env": environment,
                    }
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
