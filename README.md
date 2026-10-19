# Pairo Loops

Pairo Loops is an asyncio-native orchestration layer and Textual interface for
provider-neutral development agents. An `AgentConfiguration` describes a
provider persona; an `Agent` owns one active provider session and turn; the
session repository remains the authoritative catalog of durable sessions.

See the [feature reference](src/docs/feature-reference.md) for the complete
component and lifecycle contracts.

## Setup

Python 3.10 or later is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

Authenticate the local Codex CLI before starting Codex-backed agents.

## Example

```python
import asyncio

from pairo import (
    AgentConfiguration,
    ApprovalMode,
    CodexAgent,
    SandboxMode,
    run_agents,
)
from pairo.core.events import EventPipeline
from pairo.ui import SqliteSessionRepository, TextualPermissionRequester


async def main() -> None:
    pipeline = EventPipeline()
    repository = SqliteSessionRepository()
    permissions = TextualPermissionRequester(pipeline, repository)
    configuration = AgentConfiguration(
        name="reviewer",
        sandbox=SandboxMode.WORKSPACE_WRITE,
        workspace=".",
        model="gpt-5.6",
        approval_mode=ApprovalMode.USER,
        permission_requester=permissions,
    )
    agent = CodexAgent(configuration, pipeline)
    await run_agents({configuration.id: agent}, pipeline, repository)


asyncio.run(main())
```

`run_agents` creates and persists each initial session concurrently, launches
the UI, and closes agents and persistence on success or failure. The pipeline
has no lifecycle of its own.

## Sessions and commands

Only the active session is retained by an agent. The SQLite repository stores
all session metadata and resolves a session switch before calling
`agent.activate_session(...)`.

- `/session new [NAME]` creates and selects a session.
- `/session switch NAME` activates a persisted session.
- `/sessions` lists repository sessions.
- `/compact` compacts the current idle session.
- `/delete [NAME]` deletes metadata and transcript history, then selects or
  creates a replacement when necessary.
- `/interrupt` requests interruption of the active generation.

Provider identifiers are intentionally opaque outside their agent adapter.
Codex persists only `thread_id`; fake sessions persist only `session_id`.

## Events and approvals

Construct `EventPipeline` on the running principal asyncio loop. `subscribe()`
creates an independent buffered subscription task, `push()` queues an event for
every matching subscription without waiting, and `listen()` is a one-event
convenience built from a temporary subscription. Delivery is FIFO within one
subscription; separate subscriptions may run concurrently. Events are
transient and are neither retained nor replayed.

`Subscription.wait_until_cancelled()` allows a coroutine to wait for another
callback or owner to unsubscribe it.

`SynchronousEventPipeline` is the separate SDK-thread bridge. Its `push()`
schedules publication with `call_soon_threadsafe()`, while `run()` submits a
coroutine to the principal loop and blocks only the external calling thread.

The Codex SDK invokes approval callbacks synchronously on its own callback
thread. `CodexAgent` translates the provider request, runs the configured
asynchronous `PermissionRequester`, and translates its decision back to Codex.
`CodexAgent` owns the `SynchronousEventPipeline` used for the UI-specific
handoff while the SDK callback thread waits for the asynchronous requester's
decision.

## Tests

From `src`:

```powershell
python -m pytest -q --basetemp .verify-tmp\pytest
mypy --strict pairo
python scripts\quality_gate.py
```
