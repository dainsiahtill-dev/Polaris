# ADR-0095: Runtime Realtime Single Rail Nat-JetStream

Status: Accepted

Date: 2026-06-20

## Context

Polaris runtime observation had accumulated multiple delivery paths:

- runtime.v2 WebSocket consumers backed by Nat-JetStream.
- process-local fanout queues for file and task events.
- signal-hub file watchers that caused WebSocket loops to rescan runtime files.
- removed HTTP SSE endpoints that still appeared in historical docs and tests.

This split made the UI unreliable: some workspaces showed only pending/completed
state, in-progress updates depended on local process state, and future changes
could accidentally reintroduce polling or SSE as a fallback.

## Decision

1. Product realtime delivery has one rail only:
   `Nat-JetStream -> JetStreamConsumerManager -> /v2/ws/runtime runtime.v2`.
2. Product WebSocket loops must not consume process-local fanout, signal-hub
   watchers, file polling, timer HTTP polling, HTTP long polling, or SSE.
3. Durable files remain facts and query sources, but they are not realtime
   push sources. History is fetched by explicit runtime.v2 query or initial
   HTTP snapshot, never by automatic WebSocket file scans.
4. Status changes must publish runtime.v2 status events, such as
   `status.workflow` or `status.process`, so the WebSocket can push a fresh
   status payload after a JetStream event arrives.
5. File edit events must publish `event.file_edit` runtime.v2 envelopes to
   JetStream. Legacy MessageBus/process-local adapters may remain as isolated
   internal compatibility code, but they are not a product realtime transport.
6. Static guards must block product code from importing or calling
   `REALTIME_SIGNAL_HUB`, `RUNTIME_EVENT_FANOUT`, `LOG_REALTIME_FANOUT`,
   `wait_for_update`, `ensure_watch`, `send_all_snapshots`, or
   `send_incrementals` in the runtime delivery path.

## Consequences

- A missing or unavailable JetStream consumer is a visible runtime.v2 error,
  not an implicit downgrade to polling/SSE.
- UI pages must subscribe through `RuntimeTransportProvider` and
  `runtimeSocketManager`; direct EventSource or HTTP refresh loops are defects.
- Some legacy tests that asserted startup journal snapshots or process-local
  fanout registration are inverted into no-legacy-rail guards.
- Provider-facing SSE parsing can remain inside LLM provider adapters because
  it is upstream provider protocol handling, not Polaris product realtime
  transport.

## Verification

- Static guard for frontend EventSource/polling fallbacks.
- Static guard for backend runtime delivery files and writer/status/file-edit
  publishers.
- Runtime WebSocket tests for runtime.v2 SUBSCRIBE, explicit EVENT query,
  JetStream push forwarding, dropped-event resync, and no process-local
  registration.
- Playwright audit for Home, Factory, PM, ChiefEngineer, Director, and ContextOS
  realtime updates before closing the full Factory L1-L8 verification.
