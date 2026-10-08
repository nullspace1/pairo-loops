# Pairo feature and configuration reference

This document describes the behavior implemented in `pairo/`. It is an
implementation reference rather than a roadmap: items in **Known mismatches and
caveats** are current constraints, not supported features.

## Feature map

### Component map

| Layer | Component | Responsibility | Main interfaces |
| --- | --- | --- | --- |
| Application lifecycle | `pairo.runtime` | Starts all agents, runs the Textual application, and closes agents, storage, and the event pipeline | `run_agents(...)` |
| Agent orchestration | `pairo.core.agent` | Owns user-visible sessions, queues messages, tracks lifecycle state, steers active turns, and publishes neutral events | `Agent.run`, `create_session`, `restore_session`, `switch_session`, `send_message`, `wait_for_message`, `cancel_generation`, `close` |
| Provider boundary | `pairo.core.agent_server` | Defines opaque session identifiers and the provider client contract | `AgentClient`, `SessionIdentifier`, `SessionNotification` |
| Codex adapter | `pairo.core.clients.codex_client` | Converts neutral configuration and events to and from the synchronous Codex SDK | `CodexAgentClient`; `CodexSessionIdentifier` |
| Configuration | `pairo.core.agent_config` | Validates launch settings, hooks, paths, and approval policy | `AgentConfiguration`, `Hook`, configuration enums |
| MCP connections | `pairo.core.mcp` | Models HTTP/stdio connections, tool approval policy, and local HTTP server lifecycle | `HttpMCPServer`, `StdioMCPServer`, `MCPPermissions`, `MCPHttpWrapper` |
| Event delivery | `pairo.core.events` | Serializes publication, invokes sync/async listeners, allows transformation or consumption, and resolves one-shot subscriptions | `EventPipeline.add_listener`, `remove_listener`, `listen`, `publish`, `close` |
| Permissions | `pairo.core.permissions`, `pairo.ui.permissions` | Translates provider approval requests and routes them through persisted session identity to the UI | `PermissionRequester.request`, `TextualPermissionRequester`, `build_permission_response` |
| Persistence | `pairo.ui.sqlite_session_repository` | Stores opaque identifiers, session metadata, and ordered transcript entries | `SqliteSessionRepository`; `SessionRepository` protocol |
| Commands | `pairo.ui.commands`, `pairo.ui.chat_commands` | Routes normal input and slash commands to a selected agent | `AgentCommandController.execute` |
| Textual UI | `pairo.ui.app`, `pairo.ui.widgets` | Provides agent tabs, transcripts, session sidebars, detail filtering, and approval controls | `AgentChatApp`, `run_ui` |
| Presentation | `pairo.ui.formatting`, `pairo.ui.presentation` | Converts neutral events and approval/activity payloads into transcript entries | `CompletedEventFormatter`, presenter classes |
| Agent communication | `pairo.mcp.chatroom` | Exposes MCP tools that identify callers, list memberships, and forward messages to registered agents | `Chatroom`, tools `me`, `get_chatrooms`, `send_message` |
| MCP factories | `pairo.mcp.github`, `pairo.mcp.context7` | Builds preconfigured stdio MCP connection models | `get_github_mcp_server`, `context7` |

### Primary execution flows

#### Application lifecycle

1. `run_agents` requires at least one agent and calls `run(initial_session_name)`
   on every agent concurrently.
2. Each agent creates its initial provider session and starts its message worker.
3. `run_ui` owns the interactive Textual loop.
4. On normal exit or failure, all agents are closed, then the repository, then
   the event pipeline.

The caller must construct agents with the same `EventPipeline` supplied to
`run_agents`. Permission requesters must use the same `SessionRepository` that
is supplied to `run_agents`.

#### Message and turn lifecycle

1. `Agent.send_message` validates the message and source, publishes
   `MessageSent`, and queues the text.
2. The worker calls `AgentClient.start`; later queued messages are passed to
   `AgentClient.steer` while provider notifications are being consumed.
3. Provider notifications become `AgentEvent` values. Every changed opaque
   identifier becomes a `SessionUpdated` event.
4. A completed response is queued for `wait_for_message`, published as a
   `MessageSent` from the agent, and stored by the UI. Provider failures become
   `AgentError` events and are re-raised by `wait_for_message`. An interrupted
   terminal event deliberately does neither.
5. A terminal provider event returns the agent to `idle`, unless another
   message remains queued.

#### Session persistence and restoration

1. `AgentCommandController` listens for `SessionUpdated` and upserts the opaque
   identifier by `(agent_name, session_name)`.
2. The UI appends formatted transcript entries to that registered session.
3. Selecting an unloaded session reads its identifier from the repository and
   calls `Agent.restore_session`; selecting an already loaded session calls
   `Agent.switch_session`.
4. Session identifiers remain provider-owned JSON. Generic orchestration and
   persistence serialize them but do not inspect their fields.

#### Interactive permission flow

1. The Codex adapter translates a known SDK approval method into an
   `ApprovalRequest` associated with the current opaque identifier.
2. `TextualPermissionRequester` resolves that identifier to a persisted named
   session and publishes a `PermissionRequest` with a generated UUID.
3. The matching agent tab queues the request and publishes a
   `PermissionResponse` after the user chooses a decision.
4. The requester resolves its waiter and the adapter translates the neutral
   decision back to the Codex wire response. Unknown provider methods, or
   requests received before a requester/session identifier is available, are
   declined. A *known* request whose identifier cannot be found in the
   repository instead fails the requester call; it is not converted to a
   decline.

#### Chatroom message flow

1. A new Codex session receives the originating agent name as
   `X-Agent-NAME`: an HTTP header for HTTP MCP servers or an environment value
   for stdio MCP servers.
2. The chatroom MCP server reads this identity and verifies that the sender is
   a member of the named room.
3. `send_message` calls the destination agent with source identity preserved
   and text formatted as
   `[chatroom: ROOM] Message from SENDER: MESSAGE`.

## Public and externally testable interfaces

Pairo does not expose a REST application API. Its external surfaces are Python
APIs, a Textual command interface, the streamable-HTTP MCP endpoint, and MCP
tools.

### Python interfaces

All operations below are ordinary in-process Python interfaces. They are the
most useful test seams: callers can supply an `AgentClient` test double, an
in-memory `SqliteSessionRepository()`, a deterministic repository clock, and
event-pipeline listeners without starting Textual, Codex, or an MCP server.

| Surface | Testable operations |
| --- | --- |
| `Agent` | Create, restore, switch, serialize, message, steer through queued input, interrupt, wait, inspect state, and close sessions |
| `AgentClient` | `new_session`, `restore_session`, `start`, `steer`, `interrupt`, `notifications`, `close` |
| `EventPipeline` | Listener registration/removal, one-shot subscription, ordered publication, transformation/consumption, close |
| `SessionRepository` | `save_session`, `append_entry`, `entries_for`, `get_session`, `sessions_for`, `session_for_identifier`, `close` |
| Permissions | `PermissionRequester.request`, `build_permission_response`, Codex request/decision translation |
| Runtime/UI | `run_agents`, `run_ui`, `AgentChatApp` Textual message handlers |
| MCP hosting | `MCPHttpWrapper.boot_up`, `as_http_mcp_server`, `close`, `is_running` |

`AgentClient` is the provider boundary. In particular, a fake client must
return frozen `SessionIdentifier` models from its async session operations and
yield `SessionNotification(session, event)` values from `notifications`. The
generic `Agent` does not parse identifier fields. `CodexSessionIdentifier` is
the implementation-specific form (`provider="codex"`, `thread_id`, optional
`turn_id`).

`EventPipeline.publish` returns the transformed event, or `None` when a
listener consumes it. `listen(EventType)` returns a one-shot subscription; a
single matching publication wakes every pending subscription of that type,
newest subscription first. These semantics are intentional and testable.

The event pipeline's externally observable models are:

| Event | Meaning |
| --- | --- |
| `MessageSent` | User, agent, or attributed-source text associated with a named agent session |
| `SessionUpdated` | Latest opaque provider identifier for a named session |
| `AgentEvent` | One provider-neutral client event routed to a named session |
| `AgentError` | Provider operation failure for a named session |
| `PermissionRequest` / `PermissionResponse` | UI-facing interactive approval exchange |
| `NetworkMessageSent` | Inter-agent channel delivery for presentation/persistence |
| `ImageSent` | Host-facing image-path input event |
| `UserInput` | Text intended to steer the active turn |

`AgentEvent.payload` is discriminated by `kind`; its supported values are listed
in the enum table below.

### Textual commands

| Input | Behavior |
| --- | --- |
| Plain text | Sends the trimmed message to the currently selected agent with source `user` |
| `/agent NAME` | Selects an existing agent tab |
| `/session new [NAME]` | Creates a named session; without a name, chooses the lowest unused `Conversation N` |
| `/session switch NAME` | Switches to a loaded session or restores a persisted one |
| `/sessions` | Lists the current and persisted sessions for the selected agent |
| `/interrupt` | Requests interruption when generating; otherwise reports that the agent is idle |
| `/image PATH` | Publishes an `ImageSent` event; it does not validate that the path exists |
| `/help` | Displays the command summary |

### Network and MCP endpoints

| Endpoint/tool | Contract |
| --- | --- |
| `http://HOST:PORT/mcp` | Streamable-HTTP endpoint produced by `MCPHttpWrapper` after `boot_up()`; `as_http_mcp_server()` returns this URL without starting it |
| `me()` | Returns the `X-Agent-NAME` caller identity or an identity-not-configured message |
| `get_chatrooms()` | Lists rooms containing the calling agent and their registered agent names |
| `send_message(chatroom, destination_agent_name, message)` | Forwards a message when the caller and destination are registered in the room |

## Assumptions and operational constraints

- Python 3.10 or newer is required. A Codex-backed run also requires the local
  Codex CLI/SDK environment to be authenticated outside Pairo.
- `AgentConfiguration` requires `sandbox`, `workspace`, and `model`. Its
  configuration models use strict Pydantic validation and reject unknown
  fields. `workspace` is resolved to an absolute path; existence is not
  checked. `skill_folder`, when supplied, must already be a directory.
- `AgentConfiguration` and related models are frozen, but collection fields
  such as MCP lists, hook lists, and dictionaries are not deeply immutable.
- Agent names, session names, messages, message sources, model names, hook
  commands, and MCP names/endpoints or commands must be nonblank. The UI
  additionally requires at least one agent and unique agent names.
- An agent must be running before it accepts a message. Session creation,
  restoration, and switching require the agent to be idle. Session names are
  unique only within an agent. `Agent.description` is optional metadata and is
  not normalized or validated.
- `wait_for_message(timeout=...)` returns `None` on timeout. At present, an
  interrupted response does not enqueue a completion value, so a waiter can
  remain pending unless it has a timeout.
- Cancellation immediately after `send_message` can reach the provider before
  a turn ID exists. The Codex adapter requires an active turn ID for
  interruption, so callers should not assume this race is harmless.
- The Codex adapter owns one SDK client and a single-worker notification
  executor. Only one notification stream may be active for a given thread.
  Its in-process notification queue is bounded at 128 entries.
- Provider identifiers must be JSON objects. The SQLite repository stores a
  canonical JSON encoding and expects an identifier to resolve to exactly one
  session, although the database schema does not enforce identifier uniqueness.
- Repository timestamps must come from a timezone-aware clock and are stored
  in UTC. The SQLite schema is created on first connection and has no migration
  mechanism for older schemas.
- The event pipeline is in-memory. Publication is serialized; listeners run in
  registration order, may replace or consume events, and their exceptions
  propagate to the publisher. Closing cancels pending subscriptions and rejects
  new publication/listening.
- UI transcript detail defaults to `normal`; `debug` entries are retained but
  hidden until debug detail is selected. The selected detail level is shared
  across all agent panes.
- `TextualPermissionRequester` correlates responses by generated request ID.
  It currently does not verify that the response's agent and session fields
  match the original request. Anyone able to publish a matching request ID can
  resolve the waiter with a decision.
- HTTP MCP readiness is a TCP connection check, not an MCP protocol handshake.
  Wildcard bind hosts are mapped to loopback only for readiness checks; the
  exported MCP URL still contains the configured host.
- Chatroom registration is in memory. Adding an existing room name replaces
  its membership list; there is no persistence or authorization beyond the
  caller header and membership checks.
- Skill installation merges the configured folder into
  `<workspace>/.codex/skills`. The source may not be that destination or contain
  it.

## Default configuration inventory

### Agent and runtime

| Setting | Required/default | Notes |
| --- | --- | --- |
| `Agent.name` | Required | Trimmed, nonblank |
| `Agent.description` | `None` | Informational UI metadata |
| `Agent` constructor collaborators | Required | `config`, `client`, and `event_pipeline` are explicit dependencies |
| Initial agent state | `idle` | Other states are `generating` and `stopped` |
| `Agent.send_message.source` | `"user"` | Trimmed, nonblank |
| `Agent.wait_for_message.timeout` | `None` | Wait indefinitely; a finite timeout returns `None` |
| `run_agents.initial_session_name` | `"Conversation 1"` | Applied to every managed agent |

### `AgentConfiguration`

| Field | Required/default | Notes |
| --- | --- | --- |
| `sandbox` | Required | `SandboxMode` |
| `workspace` | Required | Resolved absolute `Path` |
| `model` | Required | Trimmed, nonblank string |
| `prompt` | `None` | Codex base instructions |
| `instructions` | `None` | Codex developer instructions |
| `reasoning_effort` | `None` | Omits `model_reasoning_effort`, leaving provider default |
| `web_search` | `None` | Omits `web_search`, leaving provider default |
| `approval_mode` | `auto` | `user` requires a `permission_requester` |
| `permission_requester` | `None` | Excluded from model serialization and representation |
| `mcp_servers` | Empty list | MCP names must be unique within the configuration |
| `skill_folder` | `None` | Supplied path must be an existing directory |
| `hooks` | Empty mapping | Keys are `HookType`; values are hook lists |

`Hook.command` and `Hook.is_async` are both required; neither has a default.
The external serialized key for `is_async` is `async`.

### MCP configuration

| Setting | HTTP default | Stdio default |
| --- | --- | --- |
| `transport` | `"http"` | `"stdio"` |
| `env` | Empty mapping of header name to environment-variable name | Empty environment mapping |
| `args` | Not applicable | Empty tuple |
| `startup_timeout_sec` | `10.0` | `10.0` |
| `permissions` | `MCPPermissions()` | `None` |

`MCPPermissions.default_tools_approval_mode` defaults to `approve`, and
`tool_approval_modes` defaults to an empty mapping. Consequently, HTTP MCP
servers approve all tools by default, while stdio MCP servers omit an explicit
tool policy by default.

### Local MCP wrapper and factories

| Setting | Required/default or value |
| --- | --- |
| `MCPHttpWrapper.host` | `"127.0.0.1"` |
| `MCPHttpWrapper.port` | Required; integer from `1` through `65535` |
| `MCPHttpWrapper.startup_timeout_sec` | `10.0` seconds |
| `MCPHttpWrapper.log_level` | `ERROR` |
| Streamable HTTP path | `/mcp` |
| Valid port range | `1` through `65535` |
| Startup readiness retry interval | `0.01` seconds |
| `get_github_mcp_server.port` | `8085` |
| `Chatroom.port` | Required; passed to its local `MCPHttpWrapper` base class |

The GitHub factory launches Docker with `-i --rm`, binds
`127.0.0.1:PORT:PORT`, sets `GITHUB_OAUTH_CALLBACK_PORT`, and uses
`ghcr.io/github/github-mcp-server`. `context7(api_key, startup_timeout_ms)` has
no parameter defaults; it runs `npx -y @upstash/context7-mcp --api-key KEY` and
converts the supplied timeout from milliseconds to seconds.

### Persistence and UI

| Setting | Default |
| --- | --- |
| `SqliteSessionRepository.folder` | `None`, selecting in-memory SQLite |
| Persistent database filename | `sessions.sqlite3` |
| Repository clock | Current timezone-aware UTC time |
| `AgentChatApp.session_repository` | `None`, creating an owned in-memory repository |
| Application title/subtitle | `pairo-loops` / `Multi-agent console` |
| Quit binding | `Ctrl+Q` |
| Transcript/detail level | `normal` |
| Detail choices | `normal`, `debug` |
| Sidebar refresh interval | `0.25` seconds |
| Input placeholder | `Message this agent or enter /help` |
| Approval actions | Approve once, approve session, decline |

### Codex adapter constants and policies

| Setting | Value |
| --- | --- |
| Session provider discriminator | `"codex"` |
| New/restored `turn_id` | `None` until a turn starts |
| MCP agent identity key | `X-Agent-NAME` |
| Notification executor workers | `1` |
| Notification queue capacity | `128` |
| Cross-thread coordination polling | `0.1` seconds |
| Empty final-response fallback | `Codex completed without a text response.` |
| Unknown request, or missing requester/identifier | Decline; a known request with no repository session raises |
| Granular approvals | `sandbox_approval`, `rules`, `mcp_elicitations`, `request_permissions`, and `skill_approval` are all enabled |

## Enum values

| Enum | Values |
| --- | --- |
| `AgentState` | `idle`, `generating`, `stopped` |
| `SandboxMode` (`Sandbox`) | `read-only`, `workspace-write`, `danger-full-access` |
| `ReasoningEffort` | `low`, `medium`, `high`, `xhigh` |
| `WebSearchMode` | `disabled`, `cached`, `live` |
| `ApprovalMode` | `auto`, `user` |
| `HookType` | `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`, `Interrupt` |
| `MCPToolApprovalMode` | `auto`, `prompt`, `writes`, `approve` |
| `PermissionRequestType` | `command execution`, `file change`, `additional permissions` |
| `PermissionDecision` | `accept`, `acceptForSession`, `decline` |
| `EventDetailLevel` | `normal`, `debug` |
| `MCPHttpLogLevel` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| Client event discriminator `kind` | `text_delta`, `plan_delta`, `reasoning_delta`, `activity_started`, `activity_progress`, `activity_completed`, `response_completed`, `response_interrupted` |

## Known mismatches and caveats

### Hook async keyword

`Hook` declares the Python field `is_async` and the serialization alias `async`.
Because model population by field name is enabled, supported construction is:

```python
Hook(command="prepare-workspace", is_async=True)
```

For mapping-based input, `{"command": "prepare-workspace", "async": True}` is
also valid. `async_=True` is not a declared field and is rejected because extra
fields are forbidden. Older README examples using `async_` do not describe the
implemented API.

### Restored-session MCP identity

New Codex sessions pass the agent name into MCP configuration and inject
`X-Agent-NAME`. The provider-neutral `restore_session` contract does not accept
an agent name, and `CodexAgentClient._thread_resume_params` currently builds MCP
configuration without one. Therefore restored sessions omit the HTTP header or
stdio environment value used by chatroom identity. Chatroom tools can report
`Agent name not configured.` after restoration. This is an implementation gap,
not a different documented default.

### Shallow immutability

Configuration models are frozen, but their nested lists and dictionaries can
still be mutated. Consumers should treat the complete configuration graph as
immutable after construction even though this is not enforced recursively.

### Permission-response correlation

The generated `PermissionRequest.request_id` is the only correlation key used
by `TextualPermissionRequester`. `PermissionResponse.agent_name` and
`session_name` are presentation/routing fields, not validation inputs. Hosts
that publish responses outside `AgentChatApp` must protect request IDs and
preserve their own agent/session correlation.

### Interrupted waits

`ClientResponseInterrupted` ends the current agent turn and returns the agent
to `idle` (unless queued input remains), but it does not put a value or error
onto `wait_for_message`'s completion queue. A caller waiting without a timeout
can therefore wait indefinitely after an interruption. Use a finite timeout or
observe `AgentEvent` notifications when interruption is possible.
