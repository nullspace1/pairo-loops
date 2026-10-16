# Pairo feature reference

This reference describes the current public architecture and runtime behavior.

## Components

| Layer | Component | Responsibility |
| --- | --- | --- |
| Configuration | `pairo.core.agent.Agent` | Stores an immutable UUID and name with mutable, validated configuration |
| Orchestration | `pairo.core.agent.Runner` | Owns per-agent session state and all conversation operations |
| Provider boundary | `pairo.core.clients.AgentClient` | Creates/restores sessions and starts, steers, interrupts, and observes turns |
| Codex adapter | `pairo.core.clients.CodexAgentClient` | Maps Agent configuration and neutral events to the Codex SDK |
| Application lifecycle | `pairo.runtime.run_agents` | Starts a Runner, launches the UI, and closes shared resources |
| Events | `pairo.core.events.EventPipeline` | Delivers ordered neutral events and one-shot subscriptions |
| Persistence | `pairo.ui.SqliteSessionRepository` | Stores session identifiers, metadata, and ordered transcript entries |
| UI | `pairo.ui` | Provides agent tabs, conversation commands, transcripts, and permission controls |
| Agent communication | `pairo.mcp.chatroom` | Forwards MCP chatroom messages through the Runner |
| Task coordination | `pairo.core.mcp.spec_manager` | Assigns persisted feature tasks to worker/verifier pairs |

The public package exports `Agent`, `Runner`, `AgentState`,
`SessionIdentifier`, `JsonObject`, configuration enums and hooks, client
interfaces, and `run_agents` from `pairo` and `pairo.core` where applicable.

## Configuration

`Agent.id` is an immutable UUID generated at construction. `Agent.name` is an
immutable, trimmed display label and may be shared by multiple agents. Other
settings can be changed after construction through validated setter methods:

- Scalar settings use `set_description`, `set_sandbox`, `set_workspace`,
  `set_model`, `set_base_instructions`, `set_developer_instructions`,
  `set_reasoning_effort`, `set_web_search`, `set_approval_mode`, and
  `set_permission_requester`.
- `append_base_instructions` and `append_developer_instructions` preserve
  existing instructions and append a separate trimmed block.
- MCP servers, skill folders, and hooks support `set_*`, `add_*`, and
  `remove_*` methods. Their properties expose tuples or read-only mappings.
- Every update validates a full candidate configuration before committing it.
  Failed updates leave the current configuration unchanged. MCP names remain
  unique; workspace and skill folders must exist; user approval requires a
  permission requester.

Defaults are `description=""`, reasoning effort `low`, web search `cached`,
approval mode `auto`, and empty MCP server, skill folder, and hook collections.
Workspace, model, sandbox mode, and name are required. Hook commands and async
flags are required; the serialized async field is `async`.

## Runner and client behavior

Construct one `Runner(agents, pipeline, client)` with a single shared client.
Runner keys agents and runtime state by immutable agent ID and owns session names, identifiers, worker tasks,
message queues, generation state, and last responses independently for each
agent. Use its public methods to start and close the runtime, create/restore or
switch, delete, or compact sessions, send messages, wait for responses, and
interrupt generation.
Queries such as `session_names`, `session_name`, `state`, `is_generating`,
`last_message`, and `session_identifier_json` also go through Runner.

The provider boundary accepts the Agent for both `new_session(agent)` and
`restore_session(agent, identifier_json)`. The client snapshots configuration
when a session is created or restored. Changes made later to the Agent affect
subsequently created or restored sessions, while existing provider sessions
keep their original configuration.

The shared Codex client maintains per-session context for agent identity and
permission routing, and handles notification streams for multiple agents.
Unknown permission contexts are declined. A Codex session adds the originating
agent identity to HTTP or stdio MCP configuration as `X-Agent-ID`. Skill
folders are installed into `<workspace>/.codex/skills` in configured order;
files from later folders replace same-named files from earlier folders.

## Execution flows

### Application lifecycle

1. `run_agents(runner, session_repository)` starts the Runner with the initial
   session name, defaulting to `Conversation 1` for every agent.
2. Runner creates one provider session per agent and starts its message worker.
3. `run_ui` owns the Textual loop; UI operations call Runner.
4. On exit or failure, `run_agents` closes Runner, repository, and event
   pipeline, including when an earlier cleanup step raises.

### Messages and turns

1. `Runner.send_message` validates input, publishes `MessageSent`, and queues
   it for the selected agent's active session.
2. Runner starts the first message; later queued messages steer the active turn
   in order while client notifications are consumed.
3. Provider events become concrete routed agent event values. Changed identifiers
   produce `SessionUpdated` events, and provider token metrics produce
   `ClientTokenUsageUpdated` payloads that the UI stores in session history.
4. Completed responses are published and made available to
   `wait_for_message`. Provider errors produce `AgentError` and are re-raised
   by the waiter. Interrupted turns resolve waiters with `None`.

### Session persistence and permissions

The UI persists each `SessionCreated` or `SessionUpdated` identifier under its
agent and session names. It listens for creation events so sessions created by
UI commands and the chatroom MCP tool use the same registration path. Deletion
removes both session metadata and its cascading transcript entries; deleting
the final session creates a replacement. Restoring a persisted session passes
the current Agent and stored opaque
identifier JSON to Runner and the shared client. Permission requests are routed
by provider session context to the permission requester captured for that
session.

## Python and UI interfaces

| Surface | Main operations |
| --- | --- |
| `Agent` | Validated setters; MCP/skill/hook set, add, and remove methods; `snapshot()` |
| `Runner` | `start`, `close`, `create_session`, `restore_session`, `switch_session`, `delete_session`, `compact_session`, `send_message`, `wait_for_message`, `interrupt` |
| `AgentClient` | `new_session`, `restore_session`, `start`, `steer`, `interrupt`, `compact`, `notifications`, `close` |
| `EventPipeline` | Add/remove listeners, `listen`, ordered `publish`, `close` |
| Session repository | Save/query/delete sessions and transcript entries; resolve identifiers; `close` |
| Textual commands | `/agent`, `/session`, `/sessions`, `/compact`, `/delete`, `/interrupt`, `/image`, `/help` |
| MCP hosting | `MCPHttpWrapper.boot_up`, `as_http_mcp_server`, `close` |
| Spec manager | `install_spec_manager`, `WorkerVerifierPair`; task, assignment, and review MCP tools |

The generic Runner treats provider identifiers as opaque JSON-compatible
objects. `CodexSessionIdentifier` is the adapter-specific representation.
`EventPipeline.publish` returns a transformed event or `None` if a listener
consumes it. A matching publication wakes pending one-shot subscriptions of
that event type.

## Defaults and constraints

- Python 3.10 or newer is required. Codex-backed use requires an authenticated
  local Codex CLI/SDK environment.
- Runner requires at least one Agent. Names may repeat; generated IDs identify
  agents throughout Runner, events, persistence, permissions, and MCP routing.
  The UI requires at least one agent. Session names are unique within each agent.
- A session must be created or restored before sending messages. Session
  changes require the agent to be idle.
- `Runner.wait_for_message(agent_id, timeout=None)` waits indefinitely by
  default; a finite timeout returns `None`.
- `Runner.interrupt(agent_id)` reports whether interruption was requested.
- The session repository stores canonical JSON identifiers. The SQLite schema
  is created on first connection and does not migrate older schemas.
- The event pipeline serializes publication. Listener exceptions propagate to
  the publisher; closing the pipeline cancels pending subscriptions and
  rejects new work.
- Packaged MCP implementations accept the agents they configure and call the
  validated `Agent.add_mcp_server` API internally. Registration is idempotent
  for an identical server and rejects conflicts before changing any agent.
- Chatroom registration is in memory. `add_chatroom` receives the room's Agent
  instances, attaches the HTTP server configuration, and checks the MCP caller
  header and room membership before forwarding messages or creating a named
  session for a registered destination agent.
- Spec-manager installation adds its server, role skill, and usage instructions
  to each participant. Task statuses are atomically maintained in `FEATURES.md`
  while assignments remain in memory. A dedicated verifier session survives a
  review timeout and is deleted after the eventual acceptance or rejection.

## Enum values

| Enum | Values |
| --- | --- |
| `AgentState` | `idle`, `generating`, `stopped` |
| `SandboxMode` | `read-only`, `workspace-write`, `danger-full-access` |
| `ReasoningEffort` | `low`, `medium`, `high`, `xhigh` |
| `WebSearchMode` | `disabled`, `cached`, `live` |
| `ApprovalMode` | `auto`, `user` |
| `HookType` | `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`, `Interrupt` |
| `MCPToolApprovalMode` | `auto`, `prompt`, `writes`, `approve` |
| `PermissionRequestType` | `command execution`, `file change`, `additional permissions` |
| `PermissionDecision` | `accept`, `acceptForSession`, `decline` |
| Client event `kind` | `text_delta`, `plan_delta`, `reasoning_delta`, `activity_started`, `activity_progress`, `activity_completed`, `response_completed`, `response_interrupted` |

### Agent-scoped event observers

`EventListeners(pipeline)` provides future-only observer registrations without
requiring event class imports or manual ID filters. Each `on_<event>` method
accepts an `observer` and an optional `agent` to filter one agent:

```python
from pairo.core.events import EventListeners

listeners = EventListeners(pipeline)
registration = listeners.on_message_sent(handle_message, agent)
registration.detach()  # safe to call more than once
```

Callbacks receive the concrete event and may be synchronous or asynchronous.
They are observers: their return values are ignored, so they cannot consume or
transform pipeline events. Agent matching uses immutable `Agent.id`; network
message registrations match the destination `recipient_id`. Available methods
are `on_message_sent`, `on_agent_text_delta`, `on_agent_plan_delta`,
`on_agent_reasoning_delta`, `on_agent_activity_started`,
`on_agent_activity_progress`, `on_agent_activity_completed`,
`on_agent_response_completed`, `on_agent_response_interrupted`,
`on_agent_token_usage_updated`, `on_agent_error`, `on_session_created`,
`on_session_updated`, `on_session_compacted`, `on_session_deleted`,
`on_permission_request`, `on_permission_response`, and
`on_network_message_sent`.
