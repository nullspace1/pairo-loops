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

`run_agents` binds the pipeline to the application loop, creates each initial
session concurrently, launches the UI, and closes agents, persistence, and the
pipeline on success or failure.

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

`EventPipeline.bind()` establishes the principal asyncio loop. Events may then
be enqueued from any thread, but queue operations, listeners, history, waiters,
and created tasks execute only on that loop. Notification consumers await each
event receipt, providing backpressure and preserving provider order.

The Codex SDK invokes approval callbacks synchronously on its own callback
thread. `CodexAgent` translates the provider request, invokes the configured
synchronous `PermissionRequester`, and translates its decision back to Codex.
The Textual requester owns the UI-specific handoff to the principal pipeline
loop while the SDK callback thread waits for the user's decision.

## Tests

From `src`:

```powershell
python -m pytest -q --basetemp .verify-tmp\pytest
mypy --strict pairo
python scripts\quality_gate.py
```
