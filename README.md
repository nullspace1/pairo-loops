# Pairo Loops

Pairo Loops is an asyncio-native orchestration layer and Textual interface for
provider-neutral development agents. `Agent` stores an agent's identity and
configuration, `Runner` owns conversations, and one `AgentClient` handles
provider sessions for every agent.

See the [feature reference](src/docs/feature-reference.md) for the component
map, conversation lifecycle, public interfaces, and operational constraints.

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

from pairo import Agent, ApprovalMode, CodexAgentClient, Runner, SandboxMode, run_agents
from pairo.core.events import EventPipeline
from pairo.ui import SqliteSessionRepository, TextualPermissionRequester


async def main() -> None:
    pipeline = EventPipeline()
    repository = SqliteSessionRepository()
    permissions = TextualPermissionRequester(pipeline, repository)
    agent = Agent(
        name="reviewer",
        sandbox=SandboxMode.WORKSPACE_WRITE,
        workspace=".",
        model="gpt-5.6",
        approval_mode=ApprovalMode.USER,
        permission_requester=permissions,
    )
    client = CodexAgentClient()
    runner = Runner([agent], pipeline, client)
    await run_agents(runner, repository)


asyncio.run(main())
```

## Configuration and conversations

Each Agent receives an immutable UUID in `agent.id`. Names are immutable display
labels and do not need to be unique; Runner operations and events use the agent
ID. Other configuration is changed through validated setters such as
`set_model`, `set_workspace`, and `set_approval_mode`; use
`set_*`, `add_*`, and `remove_*` methods for MCP servers, skill folders, and
hooks. Collection properties are immutable views. Each successful update is
validated atomically, including cross-field approval and permission-requester
requirements.

Packaged MCP implementations receive the agents they should configure and add
their server configuration directly. For example,
`add_github_mcp_server(agents)` and `context7(agents, api_key, timeout)` attach
their stdio configuration, while `Chatroom.add_chatroom(name, runner, agents)`
attaches the chatroom HTTP configuration and registers the room membership.

`Runner` is the entry point for creating, restoring, switching, messaging,
waiting on, and interrupting conversations. `new_session(agent)` and
`restore_session(agent, identifier_json)` receive the Agent directly. The
client snapshots its configuration at session creation or restoration, so
later Agent updates apply to subsequent sessions only.

Configure multiple skill folders with `agent.add_skill_folder(path)`. Codex
merges folders into `<workspace>/.codex/skills` in order; later folders
override earlier files with the same name. Configure lifecycle commands with
`Hook` and `HookType`, adding and removing entries through the Agent methods.

`run_agents(runner, repository)` starts initial conversations, launches the
Textual UI, and closes the Runner, repository, and event pipeline when the
application exits.

## Sessions

Session names are application-owned identities. Provider identifiers are
opaque `SessionIdentifier` values and are persisted as JSON without being
interpreted outside the provider client.

- `/session new [NAME]` creates a session. Without a name, the UI chooses the
  lowest unused `Conversation N`.
- `/session switch NAME` restores or selects a known session.
- `/sessions` lists persisted and loaded sessions.
- `/interrupt` interrupts the active generation.

The SQLite schema is intentionally new and does not migrate the former thread
schema.

## Permission approvals

Permission prompts appear inline in the agent's active session. Outstanding
requests can be answered independently; completed cards remain as decision
records in session history. Replayed history does not expose stale approval
controls.

## Tests

```powershell
python -m pytest -q
```
