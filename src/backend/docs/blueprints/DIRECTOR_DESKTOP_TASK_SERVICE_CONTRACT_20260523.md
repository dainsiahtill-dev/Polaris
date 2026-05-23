# Director Desktop Task Service Contract Blueprint

Date: 2026-05-23
Status: Implemented
Scope: Director desktop task polling frontend integration, reusing the existing Director task HTTP contract.

## Current Fact

The Director desktop workspace polls fallback task rows from:

- `GET /v2/director/tasks`
- `GET /v2/director/tasks?source={source}`

The workspace previously built these URLs inline with `apiFetchFresh`, while the frontend service layer already exposed `listDirectorTasks(source)`.

## Target Data Flow

```text
DirectorWorkspace task sync loop
  -> listDirectorTasks(source)
  -> GET /v2/director/tasks?source={source}
  -> merge fallback backend task rows with live runtime task rows
  -> render Director task board
```

## Module Responsibilities

- `src/frontend/src/services/pmService.ts`
  - Owns the typed Director task list route wrapper.

- `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
  - Uses the service wrapper for task polling.
  - Keeps existing merge behavior: backend fallback rows fill contract fields, live runtime rows own volatile state.

## Verification Plan

- Existing service tests verify exact task endpoint paths.
- Director workspace tests verify fallback rows are loaded through `listDirectorTasks`.
- Existing backend Director task route tests remain the authoritative backend contract coverage.
