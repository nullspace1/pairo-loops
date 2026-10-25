# Pairo Loops

Pairo Loops is an asyncio-native orchestration layer and Textual interface for
provider-neutral development agents. An `AgentConfiguration` describes a
provider persona; an `Agent` owns one active provider session and turn; the
session repository remains the authoritative catalog of durable sessions.

Use the [documentation map](src/docs/README.md) to choose an overview,
provider-specific architecture, feature reference, or contributor guide.

## Setup

Python 3.10 or later and the local Codex CLI are required. From the repository
root, create the project environment under `src` and install the package:

```powershell
cd src
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Authenticate the local Codex CLI before starting Codex-backed agents.

> **Packaging status:** `src/pyproject.toml` currently declares a colocated
> `README.md`, but `src/README.md` is absent. The editable-install command above
> is the intended workflow, not yet a verified clean installation, until that
> packaging metadata is corrected.

## Example

```python
import asyncio

from pairo import (
    AgentConfiguration,
    ApprovalMode,
    CodexAgent,
    SandboxPermission,
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
        sandbox=SandboxPermission.WORKSPACE_WRITE,
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

The repository's multi-agent example also includes an example-local
[SpecManager component workflow](example/SPEC_MANAGER_WORKFLOW.md) for
planner-owned task decomposition, component verification, and approved
specification changes. It is not part of the reusable `pairo` package.

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
thread. `CodexAgent` translates the provider request into a typed command,
file-change, or additional-permissions request, runs the configured asynchronous
`PermissionRequester`, and translates its decision back to Codex. Permission
cards show only the fields needed to decide; provider bookkeeping and unknown
payload fields are not displayed.
`CodexAgent` owns the `SynchronousEventPipeline` used for the UI-specific
handoff while the SDK callback thread waits for the asynchronous requester's
decision.

## Tests

From `src`, always run checks with the project interpreter:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.verify-tmp\pytest
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe scripts\quality_gate.py
```
