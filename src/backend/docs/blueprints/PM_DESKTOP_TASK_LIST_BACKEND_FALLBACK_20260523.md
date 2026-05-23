# PM Desktop Task List Backend Fallback

Date: 2026-05-23

## Finding

The PM desktop workspace already reads backend PM history and document evidence,
but its primary task board, header counts, analytics, status bar, and dialogue
context used only runtime snapshot task rows. When the runtime snapshot did not
carry tasks, the PM console could show an empty task board and zero task metrics
even though `/v2/pm/tasks` had auditable PM task registry rows.

## Contract

PM desktop task evidence must merge these sources:

- Primary live input: `tasks` prop from the desktop runtime snapshot.
- Backend fallback: `listPmTasks({ limit: 100, offset: 0 })`, backed by
  `/v2/pm/tasks`.
- Merge key: task `id`, with runtime rows taking precedence over backend rows.

The fallback only reads PM registry contracts. It must not synthesize sample
tasks or read target-project business code.

## Verification

- `src/frontend/src/services/pmService.ts`
- `src/frontend/src/app/components/pm/PMWorkspace.tsx`
- `src/frontend/src/services/__tests__/pmService.test.ts`
- `src/frontend/src/app/components/pm/PMWorkspace.test.tsx`
