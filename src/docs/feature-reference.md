# Pairo feature reference

## Core components

| Component | Responsibility |
| --- | --- |
| `AgentConfiguration` | Validated identity, model, sandbox, instructions, MCP, skills, hooks, and approvals |
| `Agent[IdentifierT]` | Async provider-neutral conversation contract with one active session |
| `CodexAgent` | Async Codex SDK mapping, notification consumption, and approval routing |
| `FakeAgent` | Deterministic asyncio-only implementation for tests and examples |
| `EventPipeline` | Principal-loop transient broadcasting through independent buffered subscriptions |
| `SynchronousEventPipeline` | Cross-thread publication and blocking coroutine submission for synchronous SDK callbacks |
| `AgentEventListeners` | Typed, optionally agent-filtered subscriptions for agent and session events |
| `SessionRepository` | Authoritative durable session catalog and transcripts |
| `TranscriptService` | Explicit transcript persistence followed by transient mutation notifications |
| `run_agents` | Startup, UI launch, and failure-safe resource cleanup |

## Optional tools and integrations

These utilities extend the core runtime without changing its ownership model.

| Tool | Purpose |
| --- | --- |
| `ConversationEventLog` | JSON Lines record of future pipeline events with explicit flush and close |
| `WorkspaceFactory` | Linked multi-directory workspace construction and per-folder sandbox configuration |
| `Chatroom` | Direct message and session routing among registered runtime agents |
| `context7` | Adds the Context7 MCP server to agent configurations |
| `add_github_mcp_server` | Adds a GitHub MCP server to agent configurations |
| `SpecManager` | Director/worker/verifier task coordination, accepted-evidence history, and optional documenter notification |

## Agent contract

Inspection is synchronous:

- `configuration() -> AgentConfiguration`
- `current_session() -> ActiveSession[IdentifierT] | None`
- `status() -> AgentStatus`

Provider operations are asynchronous:

- `new_session(name)`
- `activate_session(name, identifier_json)`
- `send_message(message, source="user")`
- `interrupt()`
- `compact()`
- `close()`

`send_message` returns once a start or steer request is accepted. Completion,
deltas, usage, activities, interruption, and errors are delivered as events.
`AgentStatus` is `inactive`, `idle`, or `generating`.

Session identifiers contain durable provider identity only. A Codex turn ID is
kept internally and may change after steering without changing the persisted
thread identifier.

## Event pipeline

Construct the pipeline on the running principal asyncio loop. Its complete API
is `listen(event_type)`, `subscribe(event_type, callback)`,
`unsubscribe(subscription)`, and `push(event)`.

Each subscription owns one buffered queue and one asyncio task. Events are
delivered FIFO to that subscription without callback overlap. Different
subscriptions are independent and can run concurrently. Matching uses class
inheritance, so a base-event subscription receives subclass events. Callback
failures are sent to the loop exception handler and do not remove the
subscription.

Every subscription exposes `wait_until_cancelled()`. The awaitable completes
when the subscription is removed, including self-unsubscription from its
callback. Callers remain responsible for ensuring that cancellation can occur;
the pipeline intentionally adds no timeout or deadlock prevention.

`wait_until_idle()` waits for a subscription's queued callbacks to finish. It
is additive to the cancellation behavior and is useful to consumers that must
observe buffered side effects before they detach.

`listen()` creates a temporary subscription and removes it after the first
event or cancellation. `push()` only buffers the event and returns immediately.
The pipeline has no history, replay, central worker, delivery receipt, or
lifecycle methods.

## Shared conversation event log

`ConversationEventLog(pipeline, output_path)` creates the output path's parent
directories and appends every **future** Pydantic event delivered by `pipeline`
to the UTF-8 JSON Lines file. It subscribes to `BaseModel`, so it captures all
agents, sessions, and future event types without replaying pipeline history or
enumerating event classes. Each line has `timestamp` (ISO-8601 UTC),
`event_type` (the concrete class name), and `payload`
(`event.model_dump(mode="json")`). The logger is independent of transcript
persistence and never publishes logging events.

Await `flush()` when readers need all already queued records visible and
durably flushed. Await `close()` during shutdown: it drains already queued
events, detaches its subscription, flushes and closes the file, and may safely
be awaited more than once. Events published after close are not recorded.

`CodexAgent` constructs its `SynchronousEventPipeline` on the pipeline's
principal loop. `push()` uses `call_soon_threadsafe()` for external threads.
`run(coroutine)` uses `run_coroutine_threadsafe()` and blocks the external
thread; calling it from the principal loop is rejected to prevent deadlock.

## Codex execution flow

1. Session creation or activation snapshots the mutable configuration and
   awaits the async SDK thread operation.
2. `send_message` publishes the input and awaits turn start or steer.
3. One agent-owned asyncio task reads the active turn and publishes each
   translated event to subscription buffers in provider order.
4. Terminal notifications publish the routed response followed by the agent's
   `MessageSent` transcript event, then return the agent to idle.
5. Shutdown closes the SDK transport before awaiting a reader that may be
   blocked in the transport.

The installed SDK version does not expose approval-handler injection on its
async constructor. A small adapter supplies its underlying synchronous client
with the callback. No private SDK access is used elsewhere.

## Persistence and integrations

The repository owns listing and deletion. UI switching loads stored metadata
and calls `activate_session`. Runtime and UI application operations explicitly
persist successful session creation and activation; pipeline events are
transient notifications. Widgets read configuration, status, and active
session directly from each runtime agent.

Chatrooms retain a mapping of registered runtime agents and directly await the
destination's message or session operation.

`WorkspaceFactory` creates a managed root and links existing source
directories into it by directory name. `configure(agent)` applies the root,
the root sandbox permission, and each source directory's permission to the
configuration. Individual linked directories cannot use
`DANGER_FULL_ACCESS`; on Windows, link creation falls back to a directory
junction when symbolic-link privilege is unavailable.

`SpecManager` configures its director and worker/verifier participants with an
MCP server and role guidance. Accepted evidence is retained in
`output/FEATURE_HISTORY.md`; an optional documenter receives that file's path
without receiving a spec-manager role. Closing the manager cancels its waiters
before stopping the HTTP transport and does not alter task-file status.

`run_agents(agents, pipeline, repository)` validates the UUID mapping, creates
and persists initial sessions concurrently, launches the UI, and closes agents
and the repository even when startup or the UI fails. It does not bind or close
the pipeline.

## Constraints

- Python 3.10 or newer.
- At least one runtime agent is required.
- Session changes and compaction require an idle agent.
- A session must be active before messaging.
- Agent names may repeat; UUIDs route application events.
- SDK transport and callback executor threads are allowed; agents themselves
  create no threads.
