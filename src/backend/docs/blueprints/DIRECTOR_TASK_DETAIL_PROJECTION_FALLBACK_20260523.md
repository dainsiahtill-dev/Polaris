# Director Task Detail Projection Fallback Blueprint

Date: 2026-05-23

## Problem

The Director desktop task board can list tasks from workflow/runtime projection rows through `GET /v2/director/tasks?source=auto|workflow`, but the task detail route `GET /v2/director/tasks/{task_id}` only queries the local Director service.

This creates a desktop contract gap: a task visible in the board can still fail the backend detail panel with 404 when it came from the workflow projection rather than the local service queue.

## Scope

- `polaris.delivery.http.v2.director`
- Focused Director v2 router tests

No target-project code is touched.

## Architecture

```text
Director desktop selection
  -> GET /v2/director/tasks/{task_id}
  -> local DirectorService.get_task(task_id)
  -> if missing:
       RuntimeProjectionService.build_async(workspace)
       _projection_task_rows(projection)
       match id/task_id/pm_task_id/metadata ids
  -> TaskResponse
```

## Design Notes

- Local service remains the first source for live execution state.
- Projection fallback only applies after a local miss.
- Matching accepts the same identifiers the projection rows expose: `id`, `task_id`, `pm_task_id`, and common metadata PM/source ids.
- If projection lookup fails, the route keeps the existing 404 behavior.

## Verification Plan

- `ruff check` and `ruff format` for Director router/test files.
- `mypy` for Director router/test files.
- `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py -v`.
- Focused frontend Director workspace tests.
- `npm run typecheck`.
- Electron E2E regression.
