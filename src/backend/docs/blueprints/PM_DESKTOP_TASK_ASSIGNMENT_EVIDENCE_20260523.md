# PM Desktop Task Assignment Evidence

Date: 2026-05-23

## Finding

The PM management backend exposes task assignment history through
`/v2/pm/tasks/{task_id}/assignments`, but the PM desktop task detail panel does
not surface that evidence. Users can inspect the task contract and backend task
detail, but cannot see which assignee, worker, or Director handoff events were
recorded for the selected PM task.

## Contract

PM task detail drill-down must show assignment evidence as a read-only backend
projection:

- Source: `GET /v2/pm/tasks/{task_id}/assignments?limit=100`.
- Visible continuity: task contract/detail remains visible while assignment
  evidence loads or fails.
- Failure mode: show the backend error as evidence; do not hide the selected
  task detail.

The panel must not create assignments or synthesize sample assignment rows.

## Verification

- `src/frontend/src/services/pmService.ts`
- `src/frontend/src/app/components/pm/PMTaskPanel.tsx`
- `src/frontend/src/services/__tests__/pmService.test.ts`
- `src/frontend/src/app/components/pm/PMTaskPanel.test.tsx`
- Existing PM management v2 backend tests for task assignment routes.
