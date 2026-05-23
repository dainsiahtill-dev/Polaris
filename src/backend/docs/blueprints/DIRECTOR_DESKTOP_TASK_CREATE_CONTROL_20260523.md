# Director Desktop Task Create Control - 2026-05-23

## Scope

- Surface the existing `POST /v2/director/tasks` route in the Director desktop task board.
- Keep the route usage explicit and auditable: the control shows the backend endpoint, submits a typed `CreateDirectorTaskPayload`, and refreshes Director task fallback rows after success.

## Implementation

- `src/frontend/src/app/components/director/DirectorTaskPanel.tsx`
  - Added a compact create strip for subject, description, priority, timeout, and submit evidence.
  - Exposes a presentational `DirectorTaskCreateDraft` callback instead of calling backend services directly.
- `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
  - Builds the canonical `CreateDirectorTaskPayload` with PM linkage metadata from the selected task when available.
  - Calls `createDirectorTask()` and then refreshes `listDirectorTaskFallbackRows()` so the task board can show backend-created rows.

## Verification Targets

- Director task panel unit test covers draft submission and creation evidence.
- Director workspace integration test covers the exact backend service payload and success evidence.
- Existing service tests continue to cover the raw `/v2/director/tasks` route mapping.
