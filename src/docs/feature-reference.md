# Pairo feature reference

## Components

| Component | Responsibility |
| --- | --- |
| `AgentConfiguration` | Validated identity, model, sandbox, instructions, MCP, skills, hooks, and approvals |
| `Agent[IdentifierT]` | Async provider-neutral conversation contract with one active session |
| `CodexAgent` | Async Codex SDK mapping, notification consumption, and approval routing |
| `FakeAgent` | Deterministic asyncio-only implementation for tests and examples |
| `EventPipeline` | Principal-loop binding, ordered cross-thread delivery, subscriptions, and coroutine submission |
| `SessionRepository` | Authoritative durable session catalog and transcripts |
| `run_agents` | Startup, UI launch, and failure-safe resource cleanup |

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

Call `bind()` from the principal running asyncio loop before publishing work.
Binding again on that loop is safe; binding another loop fails. `enqueue()` is
thread-safe and preserves acceptance order. `submit()` is the typed bridge for
synchronous callbacks that need to run a coroutine on the principal loop.

Listeners, event history, waiter completion, asyncio queue access, and task
creation occur on the bound loop. `join()` waits for accepted event delivery.
`close()` rejects new work, drains accepted events, cancels subscriptions and
pipeline-owned tasks, and never closes the application loop.

## Codex execution flow

1. Session creation or activation snapshots the mutable configuration and
   awaits the async SDK thread operation.
2. `send_message` publishes the input and awaits turn start or steer.
3. One asyncio notification task reads the active turn. It awaits delivery of
   each translated event before reading the next notification.
4. Terminal notifications publish the routed response followed by the agent's
   `MessageSent` transcript event, then return the agent to idle.
5. Shutdown closes the SDK transport before awaiting a reader that may be
   blocked in the transport.

The installed SDK version does not expose approval-handler injection on its
async constructor. A small adapter supplies its underlying synchronous client
with the callback. No private SDK access is used elsewhere.

## Persistence and integrations

The repository owns listing and deletion. UI switching loads stored metadata
and calls `activate_session`. Session create/update events are persisted by the
UI controller. Widgets read configuration, status, and active session directly
from each runtime agent.

Chatrooms retain a mapping of registered runtime agents and directly await the
destination's message or session operation. The spec manager similarly retains
director and worker/verifier agent references. Its installer updates their
configurations with the MCP server, skills, and role instructions.

`run_agents(agents, pipeline, repository)` validates the UUID mapping, binds
the pipeline, creates initial sessions concurrently, launches the UI, and
closes every resource even when startup or the UI fails.

## Constraints

- Python 3.10 or newer.
- At least one runtime agent is required.
- Session changes and compaction require an idle agent.
- A session must be active before messaging.
- Agent names may repeat; UUIDs route application events.
- SDK transport and callback executor threads are allowed; agents themselves
  create no threads.
