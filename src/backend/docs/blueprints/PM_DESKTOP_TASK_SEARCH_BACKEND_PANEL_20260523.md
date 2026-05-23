# PM Desktop Task Search Backend Panel Blueprint

Date: 2026-05-23
Status: Implemented
Scope: PM desktop task workspace frontend integration, reusing the existing PM management HTTP contract.

## Current Fact

The backend already exposes task listing, task detail, task history, Director dispatch history, and task search through:

- `GET /v2/pm/tasks`
- `GET /v2/pm/tasks/{task_id}`
- `GET /v2/pm/tasks/history`
- `GET /v2/pm/tasks/director`
- `GET /v2/pm/search/tasks?q={query}&limit={limit}`

The PM desktop task panel filtered only the task snapshot passed into the panel. Backend search results from `/v2/pm/search/tasks` were not reachable from the desktop workflow.

## Target Data Flow

```text
PMTaskPanel search input
  -> searchPmTasks(query, limit)
  -> GET /v2/pm/search/tasks
  -> render backend task search rows with id/status/score evidence
  -> user selects result
  -> normalize result into a PM task contract view
  -> TaskDetailPanel renders source, acceptance, files, dependencies, and raw payload
```

## Module Responsibilities

- `src/frontend/src/services/pmService.ts`
  - Owns the typed frontend wrapper for `/v2/pm/search/tasks`.
  - Does not create a second task source of truth.

- `src/frontend/src/app/components/pm/PMTaskPanel.tsx`
  - Keeps the local task list sourced from the current PM state snapshot.
  - Adds a separate backend search evidence strip for task search results.
  - Opens a selected result as an auditable task detail projection and marks its provenance as `pm_task_search`.

## Verification Plan

- Frontend service tests verify exact endpoint and query encoding.
- PM task panel tests verify backend search results can open task details with contract evidence.
- Existing backend PM management tests continue to cover the v2 search route contract.
