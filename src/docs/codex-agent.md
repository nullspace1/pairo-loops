# CodexAgent architecture

`CodexAgent` is the provider adapter for the async `Agent` contract. It owns an
`AsyncCodexClient`, one active `CodexSessionIdentifier`, one internal turn ID,
and at most one asyncio notification task.

Configuration conversion covers reasoning effort, web search, sandbox and
approval policy, base/developer instructions, MCP transports and approval
modes, agent identity headers, hooks, and ordered skill installation.

Provider notifications are translated into neutral client events and routed
with the active session snapshot. This prevents a delayed notification from
being attributed to a later session. Terminal responses are extracted from a
thread read and emitted as both completion and transcript message events.
The agent owns its notification asyncio task and publishes through
`EventPipeline.push()`; it does not create threads or wait for subscriber
callbacks.

The SDK's synchronous approval callback snapshots the active requester,
translates the request, invokes `PermissionRequester.request()` directly, and
translates its result back to the SDK wire format. The provider adapter does
does not publish the provider request itself. `CodexAgent` owns a
`SynchronousEventPipeline` and uses it to execute the requester's asynchronous
decision flow on the principal loop while the SDK-owned callback thread waits.
The Textual requester publishes the UI request, and concurrent responses are
correlated by request ID through recurring subscriptions.
Unknown contexts, unsupported requests, and approval failures default to
decline.

Shutdown first clears application state, then closes the async SDK transport.
Only after transport shutdown does it await the notification task, allowing a
blocked notification read to exit cleanly.
