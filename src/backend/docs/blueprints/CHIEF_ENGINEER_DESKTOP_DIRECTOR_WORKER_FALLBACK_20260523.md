# Chief Engineer Desktop Director Worker Fallback

Date: 2026-05-23

## Scope

Chief Engineer already receives realtime Director worker heartbeats from the runtime stream. The desktop must also show backend worker evidence when realtime rows are absent.

## Behavior

- Chief Engineer loads `/v2/director/workers` as a backend fallback.
- Realtime worker rows keep precedence over backend rows with the same id.
- The Director worker panel displays endpoint provenance, status, current task, completed count, failed count, and unhealthy state.
- Loading, empty, and backend-error states remain explicit.

## Verification

- Chief Engineer workspace tests cover rendering backend Director workers when realtime heartbeats are absent.
- Focused lint and Vitest validate the desktop integration.
