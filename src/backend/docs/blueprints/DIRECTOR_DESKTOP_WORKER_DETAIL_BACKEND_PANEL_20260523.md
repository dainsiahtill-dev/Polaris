# Director Desktop Worker Detail Backend Panel - 2026-05-23

## Scope

- Surface the existing `GET /v2/director/workers/{worker_id}` backend route in the Director desktop worker evidence area.
- Keep worker list polling on `GET /v2/director/workers`; detail loading is explicit and tied to the selected worker row.

## Implementation

- `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
  - Added backend worker detail state and a `getDirectorWorker(worker_id)` fetch handler.
  - Passes selected worker detail evidence into the Director task board.
- `src/frontend/src/app/components/director/DirectorTaskPanel.tsx`
  - Worker rows are selectable controls.
  - The selected worker renders an auditable backend snapshot with `/v2/director/workers/{worker_id}`, status, task linkage, health, and completed/failed counters.

## Verification Targets

- Director workspace integration test covers worker list rendering, detail selection, exact backend route invocation, and detail evidence rendering.
- Existing service tests already cover URL encoding for `getDirectorWorker`.
