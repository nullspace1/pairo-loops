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

The SDK's synchronous approval callback snapshots the active requester,
translates the request, invokes `PermissionRequester.request()` directly, and
translates its result back to the SDK wire format. The provider adapter does
not schedule approval work through the `EventPipeline`. UI requesters own any
host-specific loop handoff they need. Unknown contexts, unsupported requests,
and approval failures default to decline.

Shutdown first clears application state, then closes the async SDK transport.
Only after transport shutdown does it await the notification task, allowing a
blocked notification read to exit cleanly.
