"""Codex SDK implementation of the asynchronous Agent protocol."""

from __future__ import annotations

import asyncio
import json
from collections.abc import ItemsView, KeysView, ValuesView
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from shutil import copytree
from threading import Lock
from typing import Literal, cast

from openai_codex.async_client import AsyncCodexClient
from openai_codex._goal import _GoalOperationState
from openai_codex.client import ApprovalHandler, CodexClient
from openai_codex.generated.v2_all import (
    AgentMessageDeltaNotification,
    ApprovalsReviewer,
    AskForApproval,
    CommandExecutionOutputDeltaNotification,
    CommandExecutionThreadItem,
    ContextCompactedNotification,
    FileChangeOutputDeltaNotification,
    ItemCompletedNotification,
    ItemStartedNotification,
    McpToolCallProgressNotification,
    McpToolCallThreadItem,
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

from pairo.core.agent.agent import AgentConfiguration
from pairo.core.agent.approval_mode import ApprovalMode
from pairo.core.agent.json_object import JsonObject
from pairo.core.agent.sandbox_permission import SandboxPermission
from pairo.core.agent.session_identifier import SessionIdentifier
from pairo.core.events import (
    AgentError,
    ClientActivityStarted,
    ClientCommandExecutionCompleted,
    ClientEvent,
    ClientPlanDelta,
    ClientMcpToolCallCompleted,
    ClientReasoningDelta,
    ClientResponseCompleted,
    ClientResponseInterrupted,
    ClientTextDelta,
    ClientTokenUsageUpdated,
    ClientRecognizedProviderNoOp,
    ClientUnknownProviderEvent,
    CommandExecutionCompletionStatus,
    EventPipeline,
    MessageSent,
    McpToolCallCompletionStatus,
    SessionCompacted,
    SessionCreated,
    SessionUpdated,
    SynchronousEventPipeline,
    TokenUsage,
)
from pairo.core.permissions import PermissionRequester

from .client import ActiveSession, AgentStatus, MCP_AGENT_ID_HEADER
from .codex_permissions import (
    translate_permission_decision,
    translate_permission_request,
)


class CodexSessionIdentifier(SessionIdentifier):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["codex"] = "codex"
    thread_id: str


class _ApprovalAwareAsyncCodexClient(AsyncCodexClient):
    """Add approval injection missing from the SDK's async constructor."""

    def __init__(self, approval_handler: ApprovalHandler) -> None:
        # The SDK async client is a thin wrapper over this field in 0.159.x.
        # Keeping the compatibility dependency here makes an SDK change fail
        # one focused contract test instead of leaking into CodexAgent.
        self._sync = CodexClient(approval_handler=approval_handler)


@dataclass(frozen=True, slots=True)
class _PermissionContext:
    active_session: ActiveSession[CodexSessionIdentifier]
    requester: PermissionRequester | None


class CodexAgent:
    """Own one configured Codex agent and its currently active conversation."""

    _COMPACTION_TIMEOUT_SECONDS = 30.0
    def __init__(
        self,
        configuration: AgentConfiguration,
        event_pipeline: EventPipeline,
        client: AsyncCodexClient | None = None,
    ) -> None:
        self._configuration = configuration
        self._pipeline = event_pipeline
        self._permission_pipeline = SynchronousEventPipeline(event_pipeline)
        self._client = client or _ApprovalAwareAsyncCodexClient(self._route_permission)
        self._client_started = False
        self._client_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._active_session: ActiveSession[CodexSessionIdentifier] | None = None
        self._turn_id: str | None = None
        self._notification_task: asyncio.Task[None] | None = None
        self._compaction_task: asyncio.Task[None] | None = None
        self._status = AgentStatus.INACTIVE
        self._closed = False
        self._permission_lock = Lock()
        self._permission_context: _PermissionContext | None = None

    def configuration(self) -> AgentConfiguration:
        return self._configuration

    def current_session(self) -> ActiveSession[CodexSessionIdentifier] | None:
        return self._active_session

    def status(self) -> AgentStatus:
        return self._status

    async def new_session(self, name: str) -> ActiveSession[CodexSessionIdentifier]:
        normalized = self._validated_session_name(name)
        try:
            async with self._state_lock:
                self._require_open()
                self._require_not_generating()
                configuration = self._configuration.snapshot()
                self._apply_configuration(configuration)
                await self._ensure_client()
                response = await self._client.thread_start(
                    self._thread_start_params(configuration)
                )
                active = ActiveSession[CodexSessionIdentifier](
                    name=normalized,
                    identifier=CodexSessionIdentifier(thread_id=response.thread.id),
                )
                self._activate(active, configuration)
        except Exception as error:
            await self._publish_session_error(normalized, error)
            raise
        self._pipeline.push(self._session_created(active))
        return active

    async def activate_session(
        self,
        name: str,
        identifier_json: JsonObject,
    ) -> ActiveSession[CodexSessionIdentifier]:
        normalized = self._validated_session_name(name)
        identifier = CodexSessionIdentifier.model_validate(identifier_json)
        try:
            async with self._state_lock:
                self._require_open()
                self._require_not_generating()
                configuration = self._configuration.snapshot()
                self._apply_configuration(configuration)
                await self._ensure_client()
                response = await self._client.thread_resume(
                    identifier.thread_id,
                    self._thread_resume_params(configuration, identifier.thread_id),
                )
                active = ActiveSession[CodexSessionIdentifier](
                    name=normalized,
                    identifier=CodexSessionIdentifier(thread_id=response.thread.id),
                )
                self._activate(active, configuration)
        except Exception as error:
            await self._publish_session_error(normalized, error)
            raise
        self._pipeline.push(self._session_updated(active))
        return active

    async def send_message(self, message: str, source: str = "user") -> None:
        normalized_message = self._validated_message(message)
        normalized_source = self._validated_source(source)
        async with self._state_lock:
            self._require_open()
            active = self._require_session()
            if self._status is AgentStatus.COMPACTING:
                raise RuntimeError("Compaction is in progress; retry after it completes.")
            self._pipeline.push(
                MessageSent(
                    agent_id=self._configuration.id,
                    session_name=active.name,
                    message=normalized_message,
                    source=normalized_source,
                )
            )
            await self._ensure_client()
            if self._status is AgentStatus.GENERATING:
                turn_id = self._require_turn_id()
                try:
                    steer_response = await self._client.turn_steer(
                        active.identifier.thread_id,
                        turn_id,
                        normalized_message,
                    )
                except Exception as error:
                    await self._publish_error(active, error)
                    raise
                else:
                    self._turn_id = steer_response.turn_id
                return
            try:
                start_response = await self._client.turn_start(
                    active.identifier.thread_id,
                    normalized_message,
                )
                self._turn_id = start_response.turn.id
                self._status = AgentStatus.GENERATING
                self._notification_task = asyncio.create_task(
                    self._consume_notifications(active),
                    name=f"codex-agent-{self._configuration.id}",
                )
            except Exception as error:
                self._status = AgentStatus.IDLE
                self._turn_id = None
                await self._publish_error(active, error)


    async def interrupt(self) -> bool:
        try:
            async with self._state_lock:
                self._require_open()
                if self._status is not AgentStatus.GENERATING:
                    return False
                active = self._require_session()
                turn_id = self._require_turn_id()
                await self._ensure_client()
                await self._client.turn_interrupt(
                    active.identifier.thread_id,
                    turn_id,
                )
                return True
        except Exception as error:
            active_session = self._active_session
            if active_session is not None:
                await self._publish_error(active_session, error)
            raise

    async def compact(self) -> None:
        active: ActiveSession[CodexSessionIdentifier] | None = None
        try:
            async with self._state_lock:
                self._require_open()
                self._require_not_generating()
                active = self._require_session()
                await self._ensure_client()
                self._status = AgentStatus.COMPACTING
                self._compaction_task = asyncio.current_task()
            operation = self._client.register_goal_operation(active.identifier.thread_id)
            try:
                await self._client.thread_compact(active.identifier.thread_id)
                try:
                    await asyncio.wait_for(
                        self._wait_for_compaction(operation, active.identifier.thread_id),
                        timeout=self._COMPACTION_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError as error:
                    raise RuntimeError(
                        "Compaction timed out before the provider confirmed completion."
                    ) from error
            finally:
                self._client.unregister_goal_operation(operation)
        except Exception as error:
            session_name = (
                self._active_session.name
                if self._active_session is not None
                else "unknown"
            )
            await self._publish_session_error(session_name, error)
            raise
        finally:
            async with self._state_lock:
                if active is not None and self._active_session == active and not self._closed:
                    self._status = AgentStatus.IDLE
                self._compaction_task = None
        if active is None:
            raise RuntimeError("Compaction did not acquire an active session.")
        self._pipeline.push(
            SessionCompacted(
                agent_id=self._configuration.id,
                session_name=active.name,
            )
        )

    async def close(self) -> None:
        async with self._state_lock:
            if self._closed:
                return
            self._closed = True
            task = self._notification_task
            compaction_task = self._compaction_task
            self._notification_task = None
            self._compaction_task = None
            self._status = AgentStatus.INACTIVE
            self._turn_id = None
            self._active_session = None
            with self._permission_lock:
                self._permission_context = None
        if compaction_task is not None and compaction_task is not asyncio.current_task():
            compaction_task.cancel()
            with suppress(asyncio.CancelledError):
                await compaction_task
        if self._client_started:
            await self._client.close()
            self._client_started = False
        if task is not None:
            with suppress(asyncio.CancelledError, Exception):
                await task

    async def _wait_for_compaction(
        self,
        operation: _GoalOperationState,
        thread_id: str,
    ) -> None:
        while True:
            notification = await self._client.next_goal_notification(operation)
            if notification.method != "thread/compacted":
                continue
            payload = cast(ContextCompactedNotification, notification.payload)
            if payload.thread_id == thread_id:
                return

    async def _ensure_client(self) -> None:
        if self._client_started:
            return
        async with self._client_lock:
            if self._client_started:
                return
            self._require_open()
            try:
                await self._client.start()
                await self._client.initialize()
            except BaseException:
                with suppress(Exception):
                    await self._client.close()
                raise
            self._client_started = True

    async def _consume_notifications(
        self,
        active: ActiveSession[CodexSessionIdentifier],
    ) -> None:
        try:
            while True:
                async with self._state_lock:
                    if self._closed or self._active_session != active:
                        return
                    turn_id = self._require_turn_id()
                notification = await self._client.next_turn_notification(turn_id)
                async with self._state_lock:
                    if self._closed or self._active_session != active:
                        return
                event = await self._translate_notification(active.identifier, notification)
                if event is None:
                    continue
                terminal = isinstance(
                    event,
                    (ClientResponseCompleted, ClientResponseInterrupted),
                )
                self._pipeline.push(
                    event.to_routed_event(self._configuration.id, active.name)
                )
                transcript_message = event.transcript_message()
                if transcript_message is not None:
                    self._pipeline.push(
                        MessageSent(
                            agent_id=self._configuration.id,
                            session_name=active.name,
                            message=transcript_message,
                            source=self._configuration.name,
                        )
                    )
                if terminal:
                    async with self._state_lock:
                        if self._active_session == active:
                            self._status = AgentStatus.IDLE
                            self._turn_id = None
                            self._notification_task = None
                    return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            async with self._state_lock:
                closing = self._closed
                if self._active_session == active:
                    self._status = AgentStatus.INACTIVE if closing else AgentStatus.IDLE
                    self._turn_id = None
                    self._notification_task = None
            if not closing:
                await self._publish_error(active, error)

    async def _translate_notification(
        self,
        identifier: CodexSessionIdentifier,
        notification: Notification,
    ) -> ClientEvent | None:
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
            root = getattr(payload.item, "root", payload.item)
            if isinstance(root, (CommandExecutionThreadItem, McpToolCallThreadItem)):
                return None
            return ClientActivityStarted(activity=self._dump(payload.item))
        if isinstance(payload, ItemCompletedNotification):
            return self._completed_activity(payload.item)
        if isinstance(payload, CommandExecutionOutputDeltaNotification):
            return None
        if isinstance(payload, McpToolCallProgressNotification):
            return None
        if isinstance(payload, FileChangeOutputDeltaNotification):
            return ClientRecognizedProviderNoOp(category="file_change_progress")
        if isinstance(payload, TurnCompletedNotification):
            if payload.turn.status is TurnStatus.interrupted:
                return ClientResponseInterrupted()
            return ClientResponseCompleted(
                message=await self._read_final_response(identifier.thread_id)
            )
        return ClientUnknownProviderEvent(
            description=f"Unknown Codex notification: {type(payload).__name__}"
        )

    def _completed_activity(self, item: object) -> ClientEvent:
        """Translate only SDK item families with an intentional typed UI contract."""
        root = getattr(item, "root", item)
        if isinstance(root, CommandExecutionThreadItem):
            return ClientCommandExecutionCompleted(
                activity_id=root.id,
                command=root.command,
                cwd=str(root.cwd) if root.cwd is not None else None,
                status=root.status.value,
                exit_code=root.exit_code,
                output=root.aggregated_output,
            )
        if isinstance(root, McpToolCallThreadItem):
            error = None if root.error is None else root.error.message
            return ClientMcpToolCallCompleted(
                activity_id=root.id,
                server=root.server,
                tool=root.tool,
                arguments=self._json_value(root.arguments),
                status=root.status.value,
                result=(
                    None if root.result is None else self._json_value(root.result)
                ),
                error=error,
            )
        return ClientUnknownProviderEvent(
            description=f"Unknown completed Codex activity: {type(root).__name__}"
        )

    async def _read_final_response(self, thread_id: str) -> str:
        response = await self._client.thread_read(thread_id, include_turns=True)
        values = cast(CodexJsonObject, response.model_dump(by_alias=True))
        messages = self._collect_text(values)
        return messages[-1] if messages else "Codex completed without a text response."

    def _apply_configuration(self, configuration: AgentConfiguration) -> None:
        for skill_folder in configuration.skill_folders:
            self._install_skills(
                skill_folder,
                configuration.workspace,
            )

    def _activate(
        self,
        active: ActiveSession[CodexSessionIdentifier],
        configuration: AgentConfiguration,
    ) -> None:
        self._active_session = active
        self._turn_id = None
        self._status = AgentStatus.IDLE
        with self._permission_lock:
            self._permission_context = _PermissionContext(
                active_session=active,
                requester=configuration.permission_requester,
            )

    def _route_permission(
        self,
        method: str,
        params: CodexJsonObject | None,
    ) -> CodexJsonObject:
        with self._permission_lock:
            context = self._permission_context
        if context is None or context.requester is None:
            return {"decision": "decline"}
        request = translate_permission_request(
            method,
            params,
            context.active_session.identifier.model_dump(mode="json"),
        )
        if request is None:
            return {"decision": "decline"}
        try:
            decision = self._permission_pipeline.run(
                context.requester.request(request)
            )
            return translate_permission_decision(request, decision)
        except Exception:
            return {"decision": "decline"}

    async def _publish_error(
        self,
        active: ActiveSession[CodexSessionIdentifier],
        error: Exception,
    ) -> None:
        await self._publish_session_error(active.name, error)

    async def _publish_session_error(
        self,
        session_name: str,
        error: Exception,
    ) -> None:
        self._pipeline.push(
            AgentError(
                agent_id=self._configuration.id,
                session_name=session_name,
                message=str(error),
            )
        )

    def _session_created(
        self,
        active: ActiveSession[CodexSessionIdentifier],
    ) -> SessionCreated:
        return SessionCreated(
            agent_id=self._configuration.id,
            session_name=active.name,
            identifier=active.identifier.model_dump(mode="json"),
        )

    def _session_updated(
        self,
        active: ActiveSession[CodexSessionIdentifier],
    ) -> SessionUpdated:
        return SessionUpdated(
            agent_id=self._configuration.id,
            session_name=active.name,
            identifier=active.identifier.model_dump(mode="json"),
        )

    def _require_session(self) -> ActiveSession[CodexSessionIdentifier]:
        if self._active_session is None:
            raise RuntimeError("The agent does not have an active session.")
        return self._active_session

    def _require_turn_id(self) -> str:
        if self._turn_id is None:
            raise RuntimeError("The Codex agent has no active turn.")
        return self._turn_id

    def _require_not_generating(self) -> None:
        if self._status in {AgentStatus.GENERATING, AgentStatus.COMPACTING}:
            raise RuntimeError("Cannot change sessions while the agent is busy.")

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("The Codex agent is closed.")

    def _validated_session_name(self, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Session name cannot be empty.")
        return normalized

    def _validated_message(self, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be empty.")
        return value

    def _validated_source(self, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Message source cannot be empty.")
        return normalized

    def _install_skills(self, skill_folder: Path, workspace: Path) -> None:
        destination = workspace / ".codex" / "skills"
        if destination == skill_folder or destination.is_relative_to(skill_folder):
            raise ValueError("skill_folder cannot contain the destination directory.")
        copytree(skill_folder, destination, dirs_exist_ok=True)

    def _token_usage(self,usage: TokenUsageBreakdown) -> TokenUsage:
        return TokenUsage(
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_output_tokens=usage.reasoning_output_tokens,
            total_tokens=usage.total_tokens,
            cache_write_input_tokens=usage.cache_write_input_tokens,
        )

    def _collect_text(self, value: CodexJsonValue) -> list[str]:
        match value:
            case dict() as mapping:
                text = mapping.get("text")
                nested = [
                    message
                    for item in mapping.values()
                    for message in self._collect_text(item)
                ]
                return ([text] if isinstance(text, str) else []) + nested
            case list() as items:
                return [
                    message
                    for item in items
                    for message in self._collect_text(item)
                ]
            case _:
                return []

    # invariant: boundary-exception[external-sdk] -- generated notification payloads expose heterogeneous runtime models
    def _dump(self, value: object) -> JsonObject:
        dumped = self._json_value(value)
        if isinstance(dumped, dict):
            return dumped
        return {"value": dumped}

    # invariant: boundary-exception[external-sdk] -- generated SDK values must cross the event boundary as JSON
    def _json_value(self, value: object) -> JsonValue:
        serialized = json.dumps(value, default=self._json_default)
        return cast(JsonValue, json.loads(serialized))

    # invariant: boundary-exception[external-sdk] -- json serialization receives unconstrained generated SDK values
    def _json_default(self, value: object) -> object:
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="python", by_alias=True, exclude_none=True)
        match value:
            case Enum() as member:
                return member.value
            case ItemsView() | KeysView() | ValuesView():
                return list(value)
            case _:
                return str(value)

    def _thread_start_params(
        self,
        config: AgentConfiguration,
    ) -> ThreadStartParams:
        return ThreadStartParams(
            approval_policy=self._approval_policy(),
            approvals_reviewer=self._reviewer(config),
            base_instructions=config.base_instructions,
            config=self._codex_config(config) or None,
            cwd=str(config.workspace),
            developer_instructions=config.developer_instructions,
            model=config.model,
            sandbox=CodexSandboxMode(config.sandbox.value),
        )

    def _thread_resume_params(
        self,
        config: AgentConfiguration,
        thread_id: str,
    ) -> ThreadResumeParams:
        return ThreadResumeParams(
            thread_id=thread_id,
            approval_policy=self._approval_policy(),
            approvals_reviewer=self._reviewer(config),
            base_instructions=config.base_instructions,
            config=self._codex_config(config) or None,
            cwd=str(config.workspace),
            developer_instructions=config.developer_instructions,
            model=config.model,
            sandbox=CodexSandboxMode(config.sandbox.value),
        )

    def _reviewer(self, config: AgentConfiguration) -> ApprovalsReviewer:
        return (
            ApprovalsReviewer.user
            if config.approval_mode is ApprovalMode.USER
            else ApprovalsReviewer.auto_review
        )

    def _approval_policy(self) -> AskForApproval:
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


    def _codex_config(self, config: AgentConfiguration) -> CodexJsonObject:
        result: CodexJsonObject = {
            "model_reasoning_effort": config.reasoning_effort.value,
            "web_search": config.web_search.value,
        }
        writable_roots = [
            str(folder)
            for folder, permission in config.folder_permissions.items()
            if permission is SandboxPermission.WORKSPACE_WRITE
        ]
        if writable_roots:
            result["sandbox_workspace_write"] = {
                "writable_roots": cast(list[CodexJsonValue], writable_roots)
            }
        if config.mcp_servers:
            servers: CodexJsonObject = {}
            for server in config.mcp_servers:
                connection: CodexJsonObject
                if server.transport == "http":
                    connection = {
                        "url": server.url,
                        "startup_timeout_sec": server.startup_timeout_sec,
                        "http_headers": {MCP_AGENT_ID_HEADER: str(config.id)},
                    }
                    if server.env:
                        connection["env_http_headers"] = dict(server.env)
                else:
                    environment: CodexJsonObject = dict(server.env or {})
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


__all__ = ["CodexAgent", "CodexSessionIdentifier"]
