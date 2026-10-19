"""Provider-neutral translation and repository-routed permission tests."""

import asyncio
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pairo.core.clients.codex_permissions import (
    translate_permission_decision,
    translate_permission_request,
)
from pairo.core.clients.codex_client import CodexAgent, CodexSessionIdentifier
from pairo.core.agent import AgentConfiguration, ApprovalMode, SandboxMode
from pairo.core.events import EventPipeline, PermissionRequest
from pairo.core.permissions import (
    PermissionDecision,
    PermissionRequester,
    PermissionRequestType,
)
from pairo.core.events import PermissionResponse
from pairo.ui.permissions import TextualPermissionRequester, build_permission_response
from pairo.ui.sqlite_session_repository import SqliteSessionRepository


class RecordingRequester(PermissionRequester):
    def __init__(self, decision: PermissionDecision) -> None:
        self.decision = decision
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        return self.decision


def test_concurrent_permission_requests_are_routed_by_identifier() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        repository = SqliteSessionRepository()
        agent_id = uuid4()
        identifier = {"provider": "fake", "thread_id": "thread-1", "turn_id": "turn-1"}
        await repository.save_session(agent_id, "Conversation 1", identifier)
        requester = TextualPermissionRequester(pipeline, repository)

        command = translate_permission_request(
            "item/commandExecution/requestApproval",
            {"command": "pytest"},
            identifier,
        )
        file_change = translate_permission_request(
            "item/fileChange/requestApproval",
            {"path": "agent.py"},
            identifier,
        )
        assert command is not None and file_change is not None
        first = asyncio.create_task(requester._request(command))
        second = asyncio.create_task(requester._request(file_change))
        while len(
            [event for event in pipeline.events if isinstance(event, PermissionRequest)]
        ) < 2:
            await asyncio.sleep(0)
        requests = [
            event for event in pipeline.events if isinstance(event, PermissionRequest)
        ]
        await pipeline.enqueue(build_permission_response(requests[1], "decline"))
        await pipeline.enqueue(build_permission_response(requests[0], "accept"))

        assert await first is PermissionDecision.ACCEPT
        assert await second is PermissionDecision.DECLINE
        assert all(request.agent_id == agent_id for request in requests)
        assert all(request.session_name == "Conversation 1" for request in requests)
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_codex_translator_owns_permission_response_wire_format() -> None:
    request = translate_permission_request(
        "item/permissions/requestApproval",
        {"permissions": {"network": {"enabled": True}}},
        {"provider": "codex", "thread_id": "thread-1", "turn_id": "turn-1"},
    )
    assert request is not None
    assert translate_permission_decision(
        request,
        PermissionDecision.ACCEPT_FOR_SESSION,
    ) == {
        "permissions": {"network": {"enabled": True}},
        "scope": "session",
    }
    assert translate_permission_decision(
        request,
        PermissionDecision.DECLINE,
    ) == {"permissions": {}}


@pytest.mark.parametrize(
    ("method", "request_type"),
    [
        ("item/commandExecution/requestApproval", PermissionRequestType.COMMAND_EXECUTION),
        ("item/fileChange/requestApproval", PermissionRequestType.FILE_CHANGE),
        ("item/permissions/requestApproval", PermissionRequestType.ADDITIONAL_PERMISSIONS),
    ],
)
def test_all_known_permission_methods_translate(method, request_type) -> None:
    request = translate_permission_request(method, None, {"id": "session"})
    assert request is not None
    assert request.request_type is request_type
    assert request.details == {}
    assert translate_permission_request("unknown/method", {}, {"id": "session"}) is None


@pytest.mark.parametrize(
    "decision",
    [PermissionDecision.ACCEPT, PermissionDecision.ACCEPT_FOR_SESSION, PermissionDecision.DECLINE],
)
def test_ordinary_permission_decisions_use_wire_values(decision) -> None:
    request = translate_permission_request(
        "item/commandExecution/requestApproval", {"command": "pytest"}, {"id": "s"}
    )
    assert request is not None
    assert translate_permission_decision(request, decision) == {"decision": decision.value}


def test_additional_permissions_ignore_non_object_permission_payloads() -> None:
    request = translate_permission_request(
        "item/permissions/requestApproval", {"permissions": "invalid"}, {"id": "s"}
    )
    assert request is not None
    assert translate_permission_decision(request, PermissionDecision.ACCEPT) == {
        "permissions": {}
    }


def test_build_permission_response_validates_decisions() -> None:
    request = PermissionRequest(
        request_id="request-1",
        agent_id=uuid4(),
        session_name="s",
        request_type=PermissionRequestType.COMMAND_EXECUTION,
        request={},
    )
    response = build_permission_response(request, "acceptForSession")
    assert response.decision is PermissionDecision.ACCEPT_FOR_SESSION
    with pytest.raises(ValueError, match="Unknown permission decision"):
        build_permission_response(request, "always")


def test_cancelled_permission_request_cleans_pending_response() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        repository = SqliteSessionRepository()
        agent_id = uuid4()
        identifier = {"provider": "fake", "thread": "thread-1"}
        await repository.save_session(agent_id, "s", identifier)
        requester = TextualPermissionRequester(pipeline, repository)
        request = translate_permission_request(
            "item/fileChange/requestApproval", {"path": "x"}, identifier
        )
        assert request is not None
        task = asyncio.create_task(requester._request(request))
        while not requester._responses:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert requester._responses == {}
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_permission_response_must_match_request_agent_and_session() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        repository = SqliteSessionRepository()
        agent_id = uuid4()
        identifier = {"provider": "fake", "thread": "thread-1"}
        await repository.save_session(agent_id, "s", identifier)
        requester = TextualPermissionRequester(pipeline, repository)
        request = translate_permission_request(
            "item/fileChange/requestApproval", {"path": "x"}, identifier
        )
        assert request is not None
        task = asyncio.create_task(requester._request(request))
        while not any(isinstance(event, PermissionRequest) for event in pipeline.events):
            await asyncio.sleep(0)
        published = next(
            event for event in pipeline.events if isinstance(event, PermissionRequest)
        )
        await pipeline.enqueue(
            PermissionResponse(
                request_id=published.request_id,
                agent_id=uuid4(),
                session_name="other",
                decision=PermissionDecision.ACCEPT,
            )
        )
        await asyncio.sleep(0)
        assert not task.done()
        await pipeline.enqueue(
            PermissionResponse(
                request_id=published.request_id,
                agent_id=published.agent_id,
                session_name=published.session_name,
                decision=PermissionDecision.ACCEPT,
            )
        )
        assert await task is PermissionDecision.ACCEPT
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_codex_agent_calls_the_permission_requester_directly() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        requester = RecordingRequester(PermissionDecision.ACCEPT)
        configured = AgentConfiguration(
            name="reviewer",
            sandbox=SandboxMode.READ_ONLY,
            workspace=".",
            model="test",
            approval_mode=ApprovalMode.USER,
            permission_requester=requester,
        )

        class Client:
            async def start(self):
                pass

            async def initialize(self):
                pass

            async def close(self):
                pass

            async def thread_resume(self, _thread_id, _params):
                return SimpleNamespace(thread=SimpleNamespace(id="thread-1"))

        adapter = CodexAgent(configured, pipeline, Client())
        await adapter.activate_session(
            "Conversation 1",
            {"provider": "codex", "thread_id": "thread-1"},
        )

        response = adapter._route_permission(
            "item/commandExecution/requestApproval",
            {"threadId": "thread-1", "command": "pytest"},
        )

        assert response == {"decision": "accept"}
        assert len(requester.requests) == 1
        assert requester.requests[0].details == {
            "threadId": "thread-1",
            "command": "pytest",
        }
        await adapter.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_textual_requester_hands_sdk_callback_to_the_principal_loop() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        repository = SqliteSessionRepository()
        identifier = {
            "provider": "codex",
            "thread_id": "thread-1",
        }
        requester = TextualPermissionRequester(pipeline, repository)
        agent = AgentConfiguration(
            name="reviewer",
            sandbox=SandboxMode.READ_ONLY,
            workspace=".",
            model="test",
            approval_mode=ApprovalMode.USER,
            permission_requester=requester,
        )
        await repository.save_session(agent.id, "Conversation 1", identifier)
        assert "permission_requester" not in agent.model_dump()

        class Client:
            async def start(self):
                pass

            async def initialize(self):
                pass

            async def close(self):
                pass

            async def thread_resume(self, _thread_id, _params):
                return SimpleNamespace(thread=SimpleNamespace(id="thread-1"))

        adapter = CodexAgent(agent, pipeline, Client())
        await adapter.activate_session("Conversation 1", identifier)
        responses = []
        callback = Thread(
            target=lambda: responses.append(
                adapter._route_permission(
                    "item/commandExecution/requestApproval",
                    {"threadId": "thread-1", "command": "pytest"},
                )
            )
        )
        callback.start()
        while not any(
            isinstance(event, PermissionRequest) for event in pipeline.events
        ):
            await asyncio.sleep(0)
        request = next(
            event for event in pipeline.events if isinstance(event, PermissionRequest)
        )
        await pipeline.enqueue(build_permission_response(request, "accept"))

        while callback.is_alive():
            await asyncio.sleep(0)
        callback.join()
        assert responses == [{"decision": "accept"}]
        await adapter.close()
        await repository.close()
        await pipeline.close()

    asyncio.run(scenario())


def test_permission_failure_defaults_to_decline() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        pipeline.bind()
        configured = AgentConfiguration(
            name="agent",
            sandbox=SandboxMode.READ_ONLY,
            workspace=".",
            model="test",
        )
        adapter = CodexAgent(configured, pipeline)
        assert adapter._route_permission(
            "item/commandExecution/requestApproval",
            {"threadId": "unknown", "command": "pytest"},
        ) == {"decision": "decline"}
        await adapter.close()
        await pipeline.close()

    asyncio.run(scenario())
