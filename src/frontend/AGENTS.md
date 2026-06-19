# Polaris Frontend Agent Rules

Status: Active
Scope: `src/frontend`

This file is a local hard gate for frontend work. It does not weaken the root
`AGENTS.md`; when rules conflict, the stricter realtime rule wins.

## Realtime Transport

1. Product realtime UI must use the unified Nat-JetStream runtime.v2 WebSocket
   transport only: `/v2/ws/runtime` through `RuntimeTransportProvider` and
   `runtimeSocketManager`.
2. Do not add or retain SSE, `EventSource`, `text/event-stream`,
   `StreamingResponse` consumption, HTTP long-polling, timer-driven fetch
   polling, file polling, or polling fallback code for realtime state.
3. Do not use `setInterval`, recursive `setTimeout`, or background timers to
   refresh app data. The only allowed timer uses are UI clocks/animations,
   copy-toast expiry, WebSocket heartbeat, reconnect backoff, and tests.
4. HTTP reads are allowed only for initial snapshot hydration, explicit user
   refresh, or a one-shot command/query response. They must not run in a loop.
5. Required realtime surfaces are the main workspace, Factory workspace,
   PM workspace, Chief Engineer workspace, Director workspace, and ContextOS
   realtime view. Changes touching these surfaces must include browser evidence
   that WebSocket push updates the page without reload and without polling.
6. New realtime channels must be added to the runtime.v2 channel/subject mapping
   and consumed through the shared transport. Components must not create a
   second realtime client.

## Audit Checklist

Before declaring frontend realtime work complete, grep the changed production
paths for:

- `EventSource`
- `text/event-stream`
- `StreamingResponse`
- `setInterval`
- `pollInterval`
- `polling`
- `轮询`

Any hit in a product data-refresh path is a failure unless it is removed or
converted to Nat-JetStream/WebSocket push. Test waits and UI-only timers must be
clearly outside realtime data delivery.
