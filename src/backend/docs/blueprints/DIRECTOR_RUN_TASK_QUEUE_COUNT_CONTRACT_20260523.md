# Director Run Task Queue Count Contract

Date: 2026-05-23

## Problem

`POST /v2/director/run` always returned `tasks_queued: 0`, even when the desktop invoked the route for a selected Director task. The response is displayed in the Director terminal, so a hard-coded value hides whether the backend accepted a task-specific execution request.

## Contract

The Director run response must report deterministic queue evidence:

- If the orchestration snapshot exposes queued tasks, use the snapshot task count.
- If a selected `task_id` was submitted but the snapshot has not expanded task rows yet, return the selected task count as a fallback.
- Preserve `0` when no selected task or snapshot task evidence exists.

The route must pass a selected `task_id` through the `tasks` argument of `OrchestrationCommandService.execute_director_run`, not only through free-form options.

## Data Flow

```text
DirectorWorkspace.runDirector()
  -> POST /v2/director/run { task_id? }
  -> Director v2 route normalizes task_ids
  -> OrchestrationCommandService.execute_director_run(tasks=task_ids)
  -> CommandResult.metadata.tasks_queued
  -> DirectorOrchestrationResponse.tasks_queued
```

## Boundaries

This change stays within `delivery/http/v2` and the existing `orchestration.pm_dispatch` public service. It does not create a new execution path or bypass the workflow runtime.

## Verification

- `src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py`
- `src/backend/polaris/tests/test_orchestration_command_service.py`
- `src/frontend/src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx`
