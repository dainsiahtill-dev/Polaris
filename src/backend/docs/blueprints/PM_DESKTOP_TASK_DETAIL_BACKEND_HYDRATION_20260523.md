# PM Desktop Task Detail Backend Hydration

Date: 2026-05-23

## Finding

The PM task panel can render local runtime task details and can open backend
search result projections, but selecting a task does not hydrate the detail
view from the existing `/v2/pm/tasks/{task_id}` route. That leaves the desktop
detail panel dependent on summarized rows from runtime projection, list
fallback, or search result payloads even when the PM task registry has the full
auditable contract.

## Contract

PM task detail drill-down must use these sources:

- Primary visible continuity: the currently selected runtime/list/search task
  projection.
- Backend hydration: `getPmTask(taskId)`, backed by `/v2/pm/tasks/{task_id}`.
- Merge rule: backend detail enriches the visible task contract; existing
  runtime projection remains visible if hydration is still loading or fails.

The panel must show endpoint provenance and backend loading/error status. It
must not synthesize sample task data or write to the target project.

## Verification

- `src/frontend/src/app/components/pm/PMTaskPanel.tsx`
- `src/frontend/src/app/components/pm/PMTaskPanel.test.tsx`
- `src/frontend/src/services/pmService.ts`
- Existing PM management v2 backend tests for `/v2/pm/tasks/{task_id}`.
