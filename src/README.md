# Pairo Loops

Pairo Loops is an asyncio-native orchestration layer and Textual interface for
provider-neutral development agents. The generic runtime depends on the
`AgentClient` protocol; Codex-specific thread IDs, turn IDs, SDK models, and
wire formats are isolated in `CodexAgentClient`.

## Architecture

See the [feature and configuration reference](docs/feature-reference.md) for
the implemented component and execution-flow map, Python/Textual/MCP test
surfaces, operational constraints, complete default-value and enum inventory,
and current behavior mismatches.

- `Agent` owns user-visible session names, async message flow, and opaque
  `SessionIdentifier` values.
- `AgentClient` creates, restores, starts, steers, interrupts, and observes
  provider sessions asynchronously.
- `CodexAgentClient` validates Codex identifiers and maps neutral
  configuration, MCP servers, permissions, and events to the Codex SDK.
- `AgentConfiguration` carries the agent's permission requester. The Textual
  requester resolves each opaque identifier through the session repository,
  so UI routing does not require binding an agent to the requester.
- `EventPipeline` provides ordered async publication and one-shot
  subscriptions.
- `SqliteSessionRepository` persists transcripts and opaque identifier JSON
  through `aiosqlite`.

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
    Agent,
    AgentConfiguration,
    ApprovalMode,
    CodexAgentClient,
    HttpMCPServer,
    Sandbox,
    run_agents,
)
from pairo.core.events import EventPipeline
from pairo.ui import SqliteSessionRepository, TextualPermissionRequester


async def main() -> None:
    pipeline = EventPipeline()
    repository = SqliteSessionRepository()
    permissions = TextualPermissionRequester(pipeline, repository)
    agent = Agent(
        name="reviewer",
        config=AgentConfiguration(
            sandbox=Sandbox.WORKSPACE_WRITE,
            workspace=".",
            model="gpt-5.6",
            approval_mode=ApprovalMode.USER,
            permission_requester=permissions,
            mcp_servers=(
                HttpMCPServer(name="docs", url="https://example.com/mcp"),
            ),
        ),
        client=CodexAgentClient(),
        event_pipeline=pipeline,
    )
    await run_agents([agent], pipeline, repository)


asyncio.run(main())
```

## Codex skills and hooks

Set `skill_folder` to a directory containing skill directories. Before a Codex
session starts, Pairo copies its contents to `<workspace>/.codex/skills`.
Configure lifecycle commands with `HookType` and `Hook`:

```python
from pairo import Hook, HookType

configuration = AgentConfiguration(
    # Other required settings...
    skill_folder="./shared-skills",
    hooks={
        HookType.SESSION_START: [
            Hook(command="prepare-workspace", is_async=True),
        ],
    },
)
```

## Local MCP servers

`MCPHttpWrapper` runs an MCP Python server with streamable HTTP and provides the
matching configuration object:

```python
from pairo import MCPHttpLogLevel, MCPHttpWrapper

class LocalMCP(MCPHttpWrapper):
    def build_mcp_server(self):
        return local_mcp_server


wrapper = LocalMCP(
    port=8765,
    startup_timeout_sec=10,
    log_level=MCPHttpLogLevel.INFO,
)
await wrapper.boot_up()
configuration = AgentConfiguration(
    # Other required settings...
    mcp_servers=(wrapper.as_http_mcp_server(),),
)

# Shutdown with the rest of the application.
await wrapper.close()
```

## Sessions

Session names are application-owned identities. Provider identifiers are opaque
Pydantic models and are persisted as JSON without being interpreted outside
their client adapter.

- `/session new [NAME]` creates a session. Without a name, the UI chooses the
  lowest unused `Conversation N`.
- `/session switch NAME` restores or selects a known session.
- `/sessions` lists persisted sessions.
- `/interrupt` interrupts the active generation.

The SQLite schema is intentionally new and does not migrate the former thread
schema.

## Permission approvals

When an agent needs permission, the prompt appears inline in that agent's active
session transcript. The complete request remains visible above its approval
buttons, and several outstanding requests can be answered independently. Once
answered, the card remains as a compact Approved once, Approved for session, or
Denied decision record while the plain-text
request and answer are retained in session history. Replayed history therefore
never exposes stale approval controls.

New transcript items scroll the transcript to the latest item after Textual has
laid them out. Long ordinary messages keep their own scroll area; pointer-wheel
events over a message or approval card stay there instead of spilling into the
surrounding transcript.

## Tests

```powershell
python -m pytest -q
```
